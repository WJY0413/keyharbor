import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
OFFICIAL = "https://chatgpt.com/backend-api/codex"


def load(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if cfg.get("schema") != 1 or not isinstance(cfg.get("keys"), list):
        raise ValueError("Invalid registry schema")
    ids, prefixes, slots = set(), set(), set()
    for key in cfg["keys"]:
        if not isinstance(key.get('display_name', key['id']), str) or len(key.get('display_name', key['id'])) > 120:
            raise ValueError('Invalid display name')
        if key.get('default_mode', 'auto') not in ('auto', 'chat', 'responses'):
            raise ValueError('Invalid default API mode')
        if not ID.fullmatch(key["id"]) or not ID.fullmatch(key["prefix"]):
            raise ValueError("Invalid key ID or alias prefix")
        if not key['prefix'].endswith('-') or key['prefix'] in ('gpt-', 'chatgpt-', 'chatgpt-web-'):
            raise ValueError('Use a dedicated private prefix ending with a hyphen')
        if key["id"] in ids or key["prefix"] in prefixes:
            raise ValueError("Duplicate key ID or alias prefix")
        if any(key['prefix'].startswith(p) or p.startswith(key['prefix']) for p in prefixes):
            raise ValueError('Overlapping private prefixes')
        slot = str(Path(key["secret_file"]).resolve()).casefold()
        if slot in slots:
            raise ValueError("Each key requires its own encrypted file")
        url = urlsplit(key["api_base"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Upstream must be a credential-free HTTPS URL")
        ids.add(key["id"]); prefixes.add(key["prefix"]); slots.add(slot)
        for upstream, alias in key.get("aliases", {}).items():
            if not ID.fullmatch(upstream) or not ID.fullmatch(alias):
                raise ValueError("Invalid model alias")
        if any(mode not in ("chat", "responses") for mode in key.get("modes", {}).values()):
            raise ValueError("Unsupported API mode")
    for key in cfg["keys"]:
        if any(f not in ids or f == key["id"] for f in key.get("fallback_keys", [])):
            raise ValueError("Invalid explicit fallback key")
    port = int(cfg.get("port", 4103))
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid local port")
    if float(cfg.get("refresh_seconds", 300)) < 5:
        raise ValueError("Refresh interval must be at least 5 seconds")
    return cfg
