import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path

from . import config, secrets

ROOT = Path(__file__).resolve().parent.parent


async def serve(registry, runtime):
    from aiohttp import web
    from .runtime import Manager
    from .gateway import app
    manager = Manager(registry, runtime)
    if not await manager.refresh():
        await manager.close()
        raise RuntimeError('Initial candidate rejected; existing gateways were not modified')
    runner = web.AppRunner(app(manager), access_log=None, auto_decompress=False)
    await runner.setup()
    try:
        await web.TCPSite(runner, '127.0.0.1', int(config.load(registry).get('port', 4103))).start()
        await manager.watch()
    finally:
        await runner.cleanup()
        await manager.close()


def main():
    parser = argparse.ArgumentParser(description='KeyHarbor: add third-party model options to Codex')
    parser.add_argument('--registry', default=str(ROOT / 'registry.json'))
    parser.add_argument('--runtime', default=str(ROOT / 'runtime'))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('serve', 'status', 'reload', 'stop'):
        sub.add_parser(name)
    admin = sub.add_parser('admin', help='Local web control panel')
    admin.add_argument('--port', type=int, default=4104)
    bridge = sub.add_parser('legacy-bridge', help='Compatibility listener backed by KeyHarbor')
    bridge.add_argument('--port', type=int, default=4105)
    reset = sub.add_parser('key-reset', help='Retry a repaired/recharged key; does not change its credential')
    reset.add_argument('id')
    key = sub.add_parser('key-set', help='Local hidden credential input; no command-line secret')
    key.add_argument('id')
    key.add_argument('--replace', action='store_true')
    for name in ('native', 'managed', 'watchdog'):
        p = sub.add_parser(name)
        p.add_argument('--config', default=str(Path.home() / '.codex' / 'config.toml'))
        p.add_argument('--backup-root', default=None)
        p.add_argument('--port', type=int, default=4103)
    args = parser.parse_args()
    if args.command == 'legacy-bridge':
        from keyharbor.legacy_bridge import run
        run(args.registry,args.port)
        return
    if args.command == 'admin':
        from .admin import run
        run(args.registry, args.runtime, args.port)
        return
    if args.command in ('native', 'managed', 'watchdog'):
        from .native import switch, watchdog
        if args.command == 'managed':
            import urllib.request
            with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/healthz', timeout=3) as response:
                state = json.load(response)
            if state.get('service') != 'cooper-litellm' or not state.get('ready'):
                raise ValueError('Managed gateway is not validated and ready')
        if args.command == 'watchdog':
            result = watchdog(args.config, args.port, args.backup_root, registry=args.registry)
        else:
            result = switch(args.config, args.command, args.port, args.backup_root, registry=args.registry)
        print(json.dumps(result, ensure_ascii=False))
        return
    cfg = config.load(args.registry)
    if args.command == 'serve':
        asyncio.run(serve(args.registry, args.runtime))
    elif args.command == 'key-set':
        profile = next((key for key in cfg['keys'] if key['id'] == args.id), None)
        if not profile:
            raise ValueError('Add a distinct profile to registry.json before supplying its key')
        value = getpass.getpass('API key (hidden, local input only): ')
        secrets.save(profile['secret_file'], value, replace=args.replace)
        value = None
        print('Encrypted credential saved for ' + args.id)
    elif args.command in ('reload', 'key-reset'):
        runtime = Path(args.runtime)
        runtime.mkdir(parents=True, exist_ok=True)
        if args.command == 'key-reset':
            if args.id not in {k['id'] for k in cfg['keys']}: raise ValueError('Unknown key')
            (runtime / 'reset-key').write_text(args.id)
        else:
            (runtime / 'reload').touch()
        print('Reload requested; inspect status for acceptance or rejection')
    else:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.get('port',4103)}/healthz", timeout=5) as response:
            status = json.load(response)
        if args.command == 'stop':
            if status.get('service') != 'cooper-litellm': raise ValueError('Port owned by another service')
            (Path(args.runtime) / 'shutdown').write_text(status['generation'])
            print('Graceful shutdown requested for the verified Cooper service')
        else:
            print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Credential-containing third-party exception messages must never reach a console.
        print(json.dumps({'error': type(exc).__name__, 'message': 'Operation failed; active production configuration was not intentionally replaced by this error.'}))
        raise SystemExit(1)
