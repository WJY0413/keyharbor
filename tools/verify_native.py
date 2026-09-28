"""Prove a fresh native Codex request without loading any gateway configuration."""
import json
import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from codex_path import resolve_codex

parser = argparse.ArgumentParser()
parser.add_argument('--model', required=True)
parser.add_argument('--codex-cli')
parser.add_argument('--managed', action='store_true')
parser.add_argument('--port',type=int,default=4103)
parser.add_argument('--web-search',action='store_true',help='Require a real web search in a harmless isolated probe')
args = parser.parse_args()
cli = resolve_codex(args.codex_cli)
env = os.environ.copy()
for name in ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'CODEX_API_KEY'):
    env.pop(name, None)
with tempfile.TemporaryDirectory(prefix='cooper-native-check-') as folder:
    overrides = ['-c', f'openai_base_url="http://127.0.0.1:{args.port}/v1"'] if args.managed else []
    if args.web_search: overrides += ['-c','web_search="live"']
    prompt = 'Use web search to find the official IANA example domains page. State its purpose briefly and cite the official source. Do not use any other tools.' if args.web_search else 'Do not use tools. Reply only NATIVE_OK.'
    result = subprocess.run([str(cli), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                             '--sandbox', 'read-only', '--json', '-C', folder, '-m', args.model, *overrides,
                             prompt],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
    events = []
    for line in result.stdout.splitlines():
        try: events.append(json.loads(line))
        except ValueError: pass
    completed = result.returncode == 0 and any(e.get('type') == 'turn.completed' for e in events)
    messages = [e.get('item',{}).get('text','') for e in events if e.get('item',{}).get('type')=='agent_message']
    searches = [e for e in events if e.get('item',{}).get('type')=='web_search' and e.get('type')=='item.completed']
    cited = any('iana.org' in text.lower() and ('https://' in text or 'cite' in text) for text in messages)
    passed = completed and (bool(searches) and cited if args.web_search else any('NATIVE_OK' in text for text in messages))
    print(json.dumps({'check':'managed_codex_real_inference' if args.managed else 'native_codex_without_user_gateway_config','model':args.model,'passed':passed,'exit_code':result.returncode,
                      'event_types':sorted({e.get('type','') for e in events}),
                      'completed_web_searches':len(searches),'official_source_cited':cited if args.web_search else None}))
    raise SystemExit(0 if passed else 1)
