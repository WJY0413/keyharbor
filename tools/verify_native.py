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
args = parser.parse_args()
cli = resolve_codex(args.codex_cli)
env = os.environ.copy()
for name in ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'CODEX_API_KEY'):
    env.pop(name, None)
with tempfile.TemporaryDirectory(prefix='cooper-native-check-') as folder:
    overrides = ['-c', 'openai_base_url="http://127.0.0.1:4103/v1"'] if args.managed else []
    result = subprocess.run([str(cli), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                             '--sandbox', 'read-only', '--json', '-C', folder, '-m', args.model, *overrides,
                             'Do not use tools. Reply only NATIVE_OK.'],
                            env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
    events = []
    for line in result.stdout.splitlines():
        try: events.append(json.loads(line))
        except ValueError: pass
    passed = result.returncode == 0 and any(e.get('type') == 'turn.completed' for e in events) and any(
        e.get('item', {}).get('type') == 'agent_message' and 'NATIVE_OK' in e['item'].get('text', '') for e in events)
    print(json.dumps({'check':'managed_codex_real_inference' if args.managed else 'native_codex_without_user_gateway_config','model':args.model,'passed':passed,'exit_code':result.returncode,
                      'event_types':sorted({e.get('type','') for e in events})}))
    raise SystemExit(0 if passed else 1)
