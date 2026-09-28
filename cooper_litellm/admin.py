"""Loopback-only management UI. No decrypted credentials in responses or logs."""
import asyncio
import hashlib
import json
import os
import secrets as random
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import tomlkit
from aiohttp import web

from . import config, secrets
from .native import switch
from .runtime import Manager

ASSETS = Path(__file__).parent / 'ui'


class Admin:
    def __init__(self, registry, runtime, codex_config=None, port=4104):
        self.registry = Path(registry).resolve()
        self.runtime = Path(runtime).resolve()
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.codex_config = Path(codex_config or Path.home() / '.codex/config.toml')
        self.port = port
        self.csrf = random.token_urlsafe(32)
        self.lock = asyncio.Lock()
        self.pending_file = self.runtime / 'pending-registry.json'

    def revision(self):
        return hashlib.sha256(self.registry.read_bytes()).hexdigest()

    async def health(self):
        cfg = config.load(self.registry)
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                result = await client.get(f"http://127.0.0.1:{cfg.get('port',4103)}/healthz")
                data = result.json()
                if data.get('service') == 'cooper-litellm': return data
        except Exception:
            pass
        return {'ready':False, 'keys':{}, 'models':[], 'last_error':{'code':'gateway_unreachable'}}

    async def state(self, request):
        cfg = config.load(self.registry)
        health = await self.health()
        try:
            doc = tomlkit.parse(self.codex_config.read_text(encoding='utf-8-sig'))
            base = doc.get('openai_base_url')
            mode = 'managed' if base == f"http://127.0.0.1:{cfg.get('port',4103)}/v1" else 'native' if not base and doc.get('model_provider','openai') == 'openai' else 'other'
        except Exception:
            mode = 'unknown'
        profiles = []
        for key in cfg['keys']:
            profile = {k:key.get(k, []) for k in ('id','prefix','api_base','visible','fallback_keys')}
            profile['display_name'] = key.get('display_name', key['id'])
            profile['default_mode'] = key.get('default_mode', 'auto')
            profile.update(enabled=key.get('enabled',True), has_secret=Path(key['secret_file']).exists(),
                           models=[alias for alias in health.get('models',[]) if alias.startswith(key['prefix'])],
                           state=health.get('keys',{}).get(key['id'], {'status':'not_loaded'}))
            profiles.append(profile)
        return web.json_response({'csrf':self.csrf, 'revision':self.revision(), 'profiles':profiles,
                                  'health':health, 'mode':mode, 'port':cfg.get('port',4103)}, headers={'Cache-Control':'no-store'})

    async def save_profile(self, body):
        cfg = config.load(self.registry)
        existing = next((k for k in cfg['keys'] if k['id'] == body.get('id')), None)
        if existing:
            key = dict(existing)
        else:
            key = {'id':body.get('id',''), 'aliases':{}, 'modes':{}, 'visible':[], 'fallback_keys':[], 'enabled':True}
        for name in ('prefix','api_base','enabled','visible','fallback_keys','display_name','default_mode'):
            if name in body: key[name] = body[name]
        if not isinstance(key.get('enabled'), bool) or not all(isinstance(key.get(n),list) and all(isinstance(x,str) for x in key[n]) for n in ('visible','fallback_keys')):
            raise ValueError('invalid_profile_fields')
        if not config.ID.fullmatch(key['id']) or not config.ID.fullmatch(key.get('prefix','')):
            raise ValueError('invalid_profile_id')
        # Model metadata never receives credentials on a different origin.
        parsed = urlsplit(key.get('api_base',''))
        key['info_url'] = f'{parsed.scheme}://{parsed.netloc}/model/info'
        supplied = body.get('secret')
        if supplied is not None and (not isinstance(supplied,str) or not supplied.strip()):
            raise ValueError('empty_credential')
        if not existing and not supplied:
            raise ValueError('new_profile_requires_credential')
        new_secret = self.runtime / 'secrets' / (key['id'] + '-' + random.token_hex(8) + '.dpapi') if supplied else None
        if new_secret: key['secret_file'] = str(new_secret)
        cfg['keys'] = [key if k['id'] == key['id'] else k for k in cfg['keys']] if existing else cfg['keys'] + [key]
        # Validate nonsecret schema before making a network request or writing a credential.
        self.pending_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        config.load(self.pending_file)
        model_count = None
        if supplied:
            manager = Manager(self.registry, self.runtime / 'validation')
            try:
                routes, _ = await manager.discover(key, supplied)
                if not routes: raise ValueError('no_supported_models')
                model_count = len(routes)
            except Exception:
                raise ValueError('credential_or_model_validation_failed') from None
            finally:
                await manager.close()
            # Save a new encrypted version: never overwrite the other profile or its old key.
            secrets.save(new_secret, supplied)
        return cfg, model_count

    async def mutate(self, request):
        if not random.compare_digest(request.headers.get('X-CSRF-Token',''), self.csrf):
            return web.json_response({'error':'csrf_rejected'}, status=403)
        body = await request.json()
        if not isinstance(body,dict): raise ValueError('json_object_required')
        async with self.lock:
            action = request.match_info['action']
            if action in ('profile','remove'):
                revision = self.revision()
                if body.get('revision') != revision:
                    return web.json_response({'error':'configuration_changed_refresh_first'}, status=409)
                if action == 'profile':
                    cfg, count = await self.save_profile(body)
                else:
                    cfg = config.load(self.registry); count = None
                    if body.get('id') not in {k['id'] for k in cfg['keys']}: raise ValueError('unknown_profile')
                    cfg['keys'] = [k for k in cfg['keys'] if k['id'] != body['id']]
                    for key in cfg['keys']:
                        key['fallback_keys'] = [v for v in key.get('fallback_keys',[]) if v != body['id']]
                    self.pending_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
                    config.load(self.pending_file)
                if self.revision() != revision:
                    return web.json_response({'error':'configuration_changed_refresh_first'}, status=409)
                backups = self.runtime / 'registry-history'; backups.mkdir(exist_ok=True)
                (backups / (str(time.time_ns()) + '.json')).write_bytes(self.registry.read_bytes())
                os.replace(self.pending_file, self.registry)
                (self.runtime / 'reload').touch()
                return web.json_response({'saved':True, 'models_verified':count, 'activation':'pending_hot_reload'})
            if action == 'reload':
                (self.runtime / 'reload').touch()
                return web.json_response({'requested':True})
            if action == 'reset':
                if body.get('id') not in {k['id'] for k in config.load(self.registry)['keys']}: raise ValueError('unknown_profile')
                (self.runtime / 'reset-key').write_text(body['id'])
                return web.json_response({'requested':True})
            if action in ('native','managed'):
                cfg = config.load(self.registry)
                if action == 'managed' and not (await self.health()).get('ready'):
                    raise ValueError('gateway_not_ready')
                return web.json_response(switch(self.codex_config, action, cfg.get('port',4103), registry=self.registry))
            return web.json_response({'error':'unknown_action'}, status=404)

    async def asset(self, request):
        name = request.match_info.get('name') or 'index.html'
        if name not in ('index.html','app.js','style.css'): raise web.HTTPNotFound()
        return web.FileResponse(ASSETS / name)

    def application(self):
        @web.middleware
        async def guard(request, handler):
            if request.host not in (f'127.0.0.1:{self.port}', f'localhost:{self.port}'):
                return web.json_response({'error':'local_host_required'}, status=403)
            origin = request.headers.get('Origin')
            if origin and origin != 'http://' + request.host:
                return web.json_response({'error':'cross_origin_rejected'}, status=403)
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return web.json_response({'error':'cross_site_rejected'}, status=403)
            try:
                result = await handler(request)
            except web.HTTPException:
                raise
            except ValueError as exc:
                allowed = {'invalid_profile_fields','invalid_profile_id','empty_credential','new_profile_requires_credential',
                           'no_supported_models','credential_or_model_validation_failed','unknown_profile','gateway_not_ready'}
                result = web.json_response({'error':str(exc) if str(exc) in allowed else 'invalid_configuration'}, status=400)
            except Exception:
                result = web.json_response({'error':'management_operation_failed'}, status=500)
            result.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                                   'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
            return result
        application = web.Application(middlewares=[guard], client_max_size=65536)
        application.router.add_get('/api/state', self.state)
        application.router.add_post('/api/{action}', self.mutate)
        application.router.add_get('/', self.asset)
        application.router.add_get('/{name}', self.asset)
        return application


def run(registry, runtime, port=4104):
    web.run_app(Admin(registry, runtime, port=port).application(), host='127.0.0.1', port=port, access_log=None, print=None)
