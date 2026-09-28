"""Resolve a local Codex executable without embedding a machine or build path."""
import os
import shutil
from pathlib import Path


def resolve_codex(explicit=None):
    requested = explicit or os.environ.get('KEYHARBOR_CODEX_CLI')
    if requested:
        path = Path(requested).expanduser()
        if not path.is_file(): raise ValueError('Specified Codex executable does not exist')
        return path
    command = shutil.which('codex.exe' if os.name == 'nt' else 'codex')
    if command: return Path(command)
    if os.name == 'nt':
        candidate = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'OpenAI/Codex/bin/codex.exe'
        if candidate.is_file(): return candidate
    raise ValueError('Provide --codex-cli or KEYHARBOR_CODEX_CLI')
