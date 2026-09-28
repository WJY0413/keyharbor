"""Independent escape hatch: no dependency on a running gateway or LiteLLM."""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import tomlkit


def switch(path, mode, port=4103, backup_root=None, registry=None):
    path = Path(path).resolve()
    original = path.read_bytes()
    doc = tomlkit.parse(original.decode('utf-8-sig'))
    if mode not in ('native', 'managed'):
        raise ValueError('Unknown mode')
    # An environment override could silently defeat the native escape hatch.
    if mode == 'native' and os.environ.get('OPENAI_BASE_URL'):
        raise ValueError('OPENAI_BASE_URL is set; remove it from the launching environment first')
    for name in ('openai_base_url', 'model_catalog_json', 'profile'):
        doc.pop(name, None)
    doc['model_provider'] = 'openai'
    providers = doc.get('model_providers', {})
    if 'openai' in providers:
        raise ValueError('Custom built-in openai provider override requires explicit review')
    registry_path = Path(registry) if registry else Path(__file__).resolve().parent.parent / 'registry.json'
    registered = json.loads(registry_path.read_text(encoding='utf-8-sig')) if registry_path.exists() else {'keys':[]}
    backup_root = backup_root or os.environ.get('KEYHARBOR_BACKUP_ROOT') or registered.get('backup_root') or Path.home() / '.keyharbor' / 'backups'
    private_prefixes = tuple(k['prefix'] for k in registered['keys']) + ('chatgpt-web/',)
    if str(doc.get('model', '')).startswith(private_prefixes):
        raise ValueError('Select an official native default model before switching')
    if mode == 'managed':
        doc['openai_base_url'] = f'http://127.0.0.1:{int(port)}/v1'
    rendered = tomlkit.dumps(doc)
    for obsolete in (
        '# Temporary catalog restores official, Web, and four routed private models in the picker.\n',
        '# Remove model_catalog_json after the live additive directory is repaired.\n',
    ):
        rendered = rendered.replace(obsolete, '')
    updated = rendered.encode('utf-8')
    if updated == original:
        return {'mode': mode, 'changed': False}
    folder = Path(backup_root) / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns())[-6:] + '-cooper-' + mode)
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path, folder / 'config.toml')
    (folder / 'manifest.json').write_text(json.dumps({
        'source': str(path), 'reason': 'Cooper LiteLLM mode switch',
        'sha256': hashlib.sha256(original).hexdigest(), 'restore': 'Copy config.toml to source while preserving any later changes',
        'excludes': ['authentication files', 'secrets', 'runtime databases'],
    }, indent=2), encoding='utf-8')
    (folder / 'MANIFEST.md').write_text(
        f'# Targeted configuration backup\n\nSource: {path}\n\nBackup: {folder / "config.toml"}\n\n'
        f'Reason: select {mode} mode.\n\nRestore: copy the backup to the source after checking for later changes.\n'
        '\nNo authentication files, credentials, databases or caches were copied.\n', encoding='utf-8')
    # Do not overwrite a concurrent editor.
    if path.read_bytes() != original:
        raise RuntimeError('Config changed concurrently; no switch applied')
    temporary = path.with_name('config.cooper-' + str(time.time_ns()) + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(updated)
    os.replace(temporary, path)
    return {'mode': mode, 'changed': True, 'backup': str(folder),
            'activation': 'New processes read this configuration. Existing Desktop sessions may require an explicitly authorized restart.'}


def watchdog(config_path, port, backup_root, interval=5, failures=3, registry=None):
    """Separate process, sticky native failover; never restarts Codex or flips back."""
    import urllib.request
    consecutive = 0
    while True:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
                state = json.load(response)
            healthy = state.get('service') == 'cooper-litellm' and state.get('ready')
            keys = list(state.get('keys', {}).values())
            if keys and not state.get('private_available', True) and all(k.get('status') in ('invalid', 'exhausted') for k in keys):
                healthy = False
        except Exception:
            healthy = False
        consecutive = 0 if healthy else consecutive + 1
        if consecutive >= failures:
            # Never overwrite a user's subsequently selected unrelated provider.
            doc = tomlkit.parse(Path(config_path).read_text(encoding='utf-8-sig'))
            if doc.get('openai_base_url') != f'http://127.0.0.1:{port}/v1':
                return {'mode': 'unchanged', 'reason': 'managed_route_not_selected'}
            return switch(config_path, 'native', backup_root=backup_root, registry=registry)
        time.sleep(interval)
