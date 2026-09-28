"""Fresh app-server model/list readback using the real selected Codex configuration."""
import asyncio
import argparse
import json
import urllib.request
from pathlib import Path
from codex_path import resolve_codex


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--codex-cli')
    args = parser.parse_args()
    cfg = json.loads((Path(__file__).resolve().parents[1]/'registry.json').read_text(encoding='utf-8-sig'))
    prefixes = tuple(k['prefix'] for k in cfg['keys'])
    expected = set()
    if not args.native:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.get('port',4103)}/healthz",timeout=3) as response:
            expected = set(json.load(response)['visible_models'])
    cli = resolve_codex(args.codex_cli)
    child = await asyncio.create_subprocess_exec(str(cli), 'app-server', stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    def send(value): child.stdin.write((json.dumps(value)+'\n').encode())
    try:
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'cooper-catalog-acceptance','version':'1.0.0'},'capabilities':{}}})
        while True:
            line = await asyncio.wait_for(child.stdout.readline(),40)
            if not line: raise ValueError('appserver_exited')
            try: message = json.loads(line)
            except ValueError: continue
            if message.get('id') == 1: send({'id':2,'method':'model/list','params':{}})
            if message.get('id') == 2:
                rows = message.get('result',{}).get('data',[])
                names = [m.get('model') or m.get('id') or m.get('slug') or '' for m in rows]
                private = {n for n in names if n.startswith(prefixes)}
                web = {n for n in names if n.startswith('chatgpt-web/')}
                expected_web = set() if args.native else set(cfg.get('web_models',[]))
                passed = not message.get('error') and bool(names) and private == expected and web == expected_web
                print(json.dumps({'check':'fresh_codex_appserver_native_model_list' if args.native else 'fresh_codex_appserver_dynamic_model_list','passed':passed,'count':len(names),'private_visible':len(private),'web':len(web)}))
                if not passed: raise ValueError('model_list_mismatch')
                return
    finally:
        if child.returncode is None: child.terminate()
        await child.wait()


if __name__ == '__main__':
    try: asyncio.run(main())
    except Exception as exc:
        print(json.dumps({'passed':False,'error_type':type(exc).__name__}))
        raise SystemExit(1)
