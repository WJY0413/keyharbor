"""Read native credentials in memory; print only assertions, never headers or bodies."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx


async def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=4103)
    p.add_argument('--inference', action='store_true')
    p.add_argument('--model', action='append', default=[], help='Explicit model alias for paid inference checks')
    args = p.parse_args()
    if args.inference and not args.model: p.error('--inference requires at least one --model')
    cfg = json.loads((Path(__file__).resolve().parents[1] / 'registry.json').read_text(encoding='utf-8-sig'))
    base = f'http://127.0.0.1:{args.port}/v1'
    auth = json.loads((Path.home() / '.codex/auth.json').read_text())['tokens']
    headers = {'Authorization': 'Bearer ' + auth['access_token'], 'ChatGPT-Account-ID': auth['account_id'],
               'originator': 'codex_cli_rs', 'User-Agent': 'codex_cli_rs/0.155.0'}
    async with httpx.AsyncClient(timeout=180) as client:
        upstream = await client.get('https://chatgpt.com/backend-api/codex/models?client_version=0.155.0', headers=headers)
        merged = await client.get(base + '/models?client_version=0.155.0', headers=headers)
        assert upstream.status_code == 200 and merged.status_code == 200, 'catalog_status_failed'
        native = upstream.json()['models']; actual = merged.json()['models']
        slugs = {m['slug'] for m in actual}
        assert all(m in actual for m in native), 'official_metadata_changed'
        health = (await client.get(f'http://127.0.0.1:{args.port}/healthz')).json()
        prefixes = tuple(k['prefix'] for k in cfg['keys'])
        expected = set(health['visible_models'])
        actual_private = {slug for slug in slugs if slug.startswith(prefixes)}
        assert actual_private == expected, 'configured_visibility_mismatch'
        print(json.dumps({'check':'live_dynamic_catalog','passed':True,'official':len(native),'merged':len(actual),
                          'private_visible':len(actual_private),'configured_profiles':len(cfg['keys'])}), flush=True)
        if not args.inference: return
        for model in args.model:
            response = await client.post(base + '/responses', json={'model':model,'input':'Reply only OK.','max_output_tokens':32,'stream':False})
            data = response.json()
            assert response.status_code == 200 and data.get('output'), 'inference_failed_' + model + '_' + str(response.status_code)
            print(json.dumps({'check':'live_responses','model':model,'passed':True,'selected_key':response.headers.get('X-Cooper-Key'),'status':data.get('status')}), flush=True)
        async with client.stream('POST', base + '/responses', json={'model':args.model[0],'input':'Reply only OK.','max_output_tokens':32,'stream':True}) as response:
            completed = False
            async for line in response.aiter_lines():
                if line.startswith('data:'):
                    try: event = json.loads(line[5:])
                    except ValueError: continue
                    completed |= event.get('type') == 'response.completed'
            assert response.status_code == 200 and completed, 'stream_failed'
            print(json.dumps({'check':'live_stream','passed':True}), flush=True)


if __name__ == '__main__':
    try: asyncio.run(main())
    except Exception as exc:
        print(json.dumps({'passed':False,'error_type':type(exc).__name__}))
        raise SystemExit(1)
