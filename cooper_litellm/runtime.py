"""Validated blue/green LiteLLM processes; atomic switching and request draining."""
import asyncio
import hashlib
import json
import os
import secrets as random_secrets
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from . import config, secrets
from .state import KeyState


class EmptyProcess:
    """All profiles explicitly disabled: native-only gateway, no stale private routes."""
    def __init__(self): self.dead = False
    def poll(self): return 0 if self.dead else None
    def terminate(self): self.dead = True
    def wait(self, timeout=None): return 0


@dataclass
class Route:
    alias: str
    key: str
    upstream: str
    mode: str
    internal: str
    visible: bool
    version: str


@dataclass
class Generation:
    id: str
    base: str
    master: str
    process: object
    routes: dict
    registry: dict
    signature: str
    inflight: int = 0
    retired: bool = False


class Manager:
    def __init__(self, registry, runtime, client=None, secret_loader=secrets.load):
        self.registry_path = Path(registry)
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(None, connect=15, write=60, pool=30), follow_redirects=False)
        self.load_secret = secret_loader
        self.current = None
        self.retired = []
        self.states = {}
        self.key_versions = {}
        self.pins = {}  # hashed session/response identifiers -> (key, secret version, expiry)
        self.last_error = None
        self.last_refresh = None
        self.lock = asyncio.Lock()
        self.stop = asyncio.Event()
        self.saved_states = {}
        try:
            self.saved_states = json.loads((self.runtime / 'key-state.json').read_text())
        except (OSError, ValueError):
            pass

    def persist_states(self):
        data = {key: {"version": self.key_versions.get(key), **vars(state)} for key, state in self.states.items()}
        dest = self.runtime / 'key-state.json'
        tmp = dest.with_suffix('.tmp')
        tmp.write_text(json.dumps(data), encoding='utf-8')
        os.replace(tmp, dest)

    async def discover(self, key, secret):
        headers = {'Authorization': 'Bearer ' + secret}
        response = await self.client.get(key['api_base'].rstrip('/') + '/models', headers=headers, timeout=20)
        if response.status_code != 200:
            raise ValueError(f"catalog_{response.status_code}")
        models = response.json().get('data')
        if not isinstance(models, list):
            raise ValueError('invalid_catalog')
        modes = dict(key.get('modes', {}))
        # Metadata informs new routes. Unknown API modes are deliberately not guessed.
        if key.get('info_url'):
            from urllib.parse import urlsplit
            if urlsplit(key['info_url']).netloc != urlsplit(key['api_base']).netloc or urlsplit(key['info_url']).scheme != 'https':
                raise ValueError('metadata_origin_mismatch')
            try:
                info = await self.client.get(key['info_url'], headers=headers, timeout=20)
                if info.status_code == 200:
                    for entry in info.json().get('data', []):
                        name, mode = entry.get('model_name'), entry.get('model_info', {}).get('mode')
                        if name not in modes and mode in ('chat', 'responses'):
                            modes[name] = mode
            except (httpx.HTTPError, ValueError, TypeError):
                pass
        routes = []
        version = hashlib.sha256(secret.encode()).hexdigest()
        for item in models:
            name = item.get('id')
            if not isinstance(name, str) or not config.ID.fullmatch(name):
                continue
            if '-anthropic' in name or name.startswith('gpt-image-'):
                continue
            mode = modes.get(name, key.get('default_mode', 'auto'))
            if mode not in ('chat', 'responses'):
                continue
            short = key.get('aliases', {}).get(name, name)
            alias = key['prefix'] + short
            visible = short in key.get('visible', [])
            internal = 'cooper_' + hashlib.sha256((key['id'] + ':' + name).encode()).hexdigest()[:24]
            routes.append(Route(alias, key['id'], name, mode, internal, visible, version))
        return routes, version

    async def build(self, cfg):
        deployments, routes, env = [], {}, {}
        key_config = {key['id']: key for key in cfg['keys']}
        for key in cfg['keys']:
            if not key.get('enabled', True):
                continue
            try:
                secret = self.load_secret(key['secret_file'])
                discovered, version = await self.discover(key, secret)
            except Exception as exc:
                # Reject edits to an existing working deployment; a new absent key is isolated.
                if self.current and any(r.key == key['id'] for r in self.current.routes.values()):
                    raise ValueError('existing_key_update_failed:' + key['id']) from None
                self.states[key['id']] = KeyState('unavailable', reason='discovery_failed')
                continue
            variable = 'COOPER_KEY_' + str(len(env))
            env[variable] = secret
            for route in discovered:
                if route.alias in routes:
                    raise ValueError('duplicate_alias')
                routes[route.alias] = route
                params = {
                    'model': 'openai/' + ('chat_completions/' if route.mode == 'chat' else '') + route.upstream,
                    'api_base': key['api_base'], 'api_key': 'os.environ/' + variable,
                }
                if route.mode == 'chat':
                    params['use_chat_completions_api'] = True
                deployments.append({'model_name': route.internal, 'litellm_params': params})
        if not routes and any(k.get('enabled', True) for k in cfg['keys']):
            raise ValueError('no_usable_keys')
        signature = hashlib.sha256(json.dumps({
            'routes': [vars(routes[k]) for k in sorted(routes)],
            'registry': cfg,
        }, sort_keys=True).encode()).hexdigest()
        if self.current and self.current.signature == signature and self.current.process.poll() is None:
            return None
        generation_id = uuid.uuid4().hex
        if not routes:
            return Generation(generation_id, '', '', EmptyProcess(), {}, cfg, signature)
        port_socket = socket.socket()
        port_socket.bind(('127.0.0.1', 0))
        port = port_socket.getsockname()[1]
        port_socket.close()
        master = 'sk-' + random_secrets.token_hex(32)
        env['COOPER_INTERNAL_KEY'] = master
        child_env = os.environ.copy()
        for inherited in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_BASE_URL'):
            child_env.pop(inherited, None)
        child_env.update(env)
        child_env['LITELLM_LOG'] = 'ERROR'
        document = {
            'model_list': deployments,
            'general_settings': {'master_key': 'os.environ/COOPER_INTERNAL_KEY'},
            'router_settings': {'num_retries': 0, 'disable_cooldowns': True},
            'litellm_settings': {'num_retries': 0, 'set_verbose': False},
        }
        file = self.runtime / ('litellm-' + generation_id + '.yaml')
        file.write_text(yaml.safe_dump(document, sort_keys=False), encoding='utf-8')
        process = subprocess.Popen(
            [sys.executable, '-m', 'litellm.proxy.proxy_cli', '--config', str(file), '--host', '127.0.0.1', '--port', str(port)],
            env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        candidate = Generation(generation_id, f'http://127.0.0.1:{port}/v1', master, process, routes, cfg, signature)
        try:
            for _ in range(90):
                if process.poll() is not None:
                    raise ValueError('litellm_start_failed')
                try:
                    response = await self.client.get(candidate.base + '/models', headers={'Authorization': 'Bearer ' + master}, timeout=1)
                    actual = {entry['id'] for entry in response.json().get('data', [])} if response.status_code == 200 else set()
                    if actual == {route.internal for route in routes.values()}:
                        return candidate
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(.5)
            raise ValueError('litellm_validation_timeout')
        except BaseException:
            process.terminate()
            await asyncio.to_thread(process.wait, 10)
            raise

    async def refresh(self):
        async with self.lock:
            try:
                cfg = config.load(self.registry_path)
                if self.current and cfg.get('port', 4103) != self.current.registry.get('port', 4103):
                    raise ValueError('port_change_requires_service_restart')
                candidate = await self.build(cfg)
                if candidate:
                    old = self.current
                    self.current = candidate
                    for route in candidate.routes.values():
                        if self.key_versions.get(route.key) != route.version:
                            saved = self.saved_states.get(route.key, {})
                            self.states[route.key] = KeyState(**{k: v for k, v in saved.items() if k != 'version'}) if saved.get('version') == route.version else KeyState()
                        self.key_versions[route.key] = route.version
                    if old:
                        old.retired = True
                        self.retired.append(old)
                    self.persist_states()
                self.last_error = None
                self.last_refresh = time.time()
                await self.reap()
                return True
            except Exception as exc:
                # Never log exception text: third-party errors can embed credentials.
                self.last_error = {'type': type(exc).__name__, 'code': 'reload_rejected'}
                return False

    async def reap(self):
        for old in list(self.retired):
            if old.inflight == 0:
                self.retired.remove(old)
                if old.process.poll() is None:
                    old.process.terminate()
                    await asyncio.to_thread(old.process.wait, 10)

    async def watch(self):
        last_marker = None
        while not self.stop.is_set():
            try:
                shutdown = self.runtime / 'shutdown'
                if shutdown.exists() and self.current and shutdown.read_text().strip() == self.current.id:
                    self.stop.set()
                    break
                reset = self.runtime / 'reset-key'
                if reset.exists():
                    key_id = reset.read_text().strip()
                    if key_id in self.states:
                        self.states[key_id].success()
                        self.persist_states()
                    reset.unlink()
                cfg = config.load(self.registry_path)
                marker = (self.registry_path.stat().st_mtime_ns, tuple(
                    (key['id'], Path(key['secret_file']).stat().st_mtime_ns if Path(key['secret_file']).exists() else 0)
                    for key in cfg['keys']), (self.runtime / 'reload').stat().st_mtime_ns if (self.runtime / 'reload').exists() else 0)
                due = not self.last_refresh or time.time() - self.last_refresh >= cfg.get('refresh_seconds', 300)
                if marker != last_marker or due or (self.current and self.current.process.poll() is not None):
                    await self.refresh()
                    last_marker = marker
                await self.reap()
            except Exception:
                self.last_error = {'code': 'registry_invalid'}
            try:
                await asyncio.wait_for(self.stop.wait(), 5)
            except asyncio.TimeoutError:
                pass

    def status(self):
        current = self.current
        active_keys = {k['id'] for k in current.registry['keys'] if k.get('enabled', True)} if current else set(self.states)
        return {
            'service': 'cooper-litellm', 'ready': bool(current and current.process.poll() is None),
            'generation': current.id if current else None,
            'inflight': current.inflight if current else 0, 'draining': len(self.retired),
            'models': sorted(current.routes) if current else [],
            'keys': {key: vars(state) for key, state in self.states.items() if key in active_keys},
            'visible_models': sorted(r.alias for r in current.routes.values() if r.visible and self.states[r.key].available()) if current else [],
            'private_available': any(self.states[r.key].available() for r in current.routes.values()) if current else False,
            'last_error': self.last_error, 'last_refresh': self.last_refresh,
        }

    async def close(self):
        self.stop.set()
        for gen in self.retired + ([self.current] if self.current else []):
            if gen.process.poll() is None:
                gen.process.terminate()
                await asyncio.to_thread(gen.process.wait, 10)
        await self.client.aclose()
