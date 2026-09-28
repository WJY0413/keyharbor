"""Inspect Git's actual index. Never read ignored credentials or print secret matches."""
import json
import re
import subprocess
from pathlib import PurePosixPath


def check():
    paths = subprocess.check_output(['git','ls-files','-z']).decode().split('\0')
    forbidden_names = {'registry.json','auth.json','runtime.local.json','ACCEPTANCE.md'}
    forbidden_parts = {'runtime','output','secrets','backups','.venv','__pycache__','.codex'}
    forbidden_suffixes = {'.dpapi','.pem','.key','.pfx','.p12','.sqlite','.db','.log','.zip'}
    patterns = [
        re.compile(rb'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{24,}'),
        re.compile(rb'gh[pousr]_[A-Za-z0-9]{20,}'),
        re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
        re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        re.compile(rb'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    ]
    violations = []
    for name in filter(None,paths):
        path = PurePosixPath(name)
        if path.name in forbidden_names or path.name.startswith('.env') or set(path.parts)&forbidden_parts or path.suffix.lower() in forbidden_suffixes:
            violations.append({'path':name,'reason':'private_file'}); continue
        # Read staged bytes, not a possibly different working-tree version.
        data = subprocess.check_output(['git','show',':'+name])
        if b'\x00' in data or len(data)>2_000_000:
            violations.append({'path':name,'reason':'unexpected_binary_or_large_file'}); continue
        if any(p.search(data) for p in patterns):
            violations.append({'path':name,'reason':'credential_pattern'})
    print(json.dumps({'passed':not violations,'tracked_files':len([p for p in paths if p]),'violations':violations}))
    return not violations


if __name__ == '__main__': raise SystemExit(0 if check() else 1)
