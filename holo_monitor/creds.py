from __future__ import annotations
import os
from typing import Optional

try:
    import keyring  # type: ignore
except Exception:  # keyring is optional
    keyring = None  # type: ignore


SERVICE = "holo_monitor"


def get_secret(name: str) -> Optional[str]:
    """Return secret from env or Windows Credential Manager (via keyring).

    - First consult environment variable `name`.
    - If missing and keyring is available, returns `keyring.get_password(SERVICE, name)`.
    """
    v = os.environ.get(name)
    if v:
        return v
    if keyring is None:
        return None
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def ensure_gcp_credentials_path() -> str:
    """Ensure GOOGLE_APPLICATION_CREDENTIALS points to a readable file and return the path.

    Resolution order:
    1) If env var GOOGLE_APPLICATION_CREDENTIALS is set and file exists -> return it.
    2) Else, if keyring contains 'GCP_SA_JSON' (raw JSON), write it to 'secrets/gcp-service-account.json',
       set env var, and return the path.
    3) Else raise RuntimeError.
    """
    path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if path and os.path.exists(path):
        return path
    # Try keyring secret content
    content = get_secret('GCP_SA_JSON')
    if content:
        out_dir = os.path.join(os.getcwd(), 'secrets')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, 'gcp-service-account.json')
        # Write only if content differs or file missing
        try:
            write = True
            if os.path.exists(out_path):
                try:
                    with open(out_path, 'r', encoding='utf-8') as f:
                        cur = f.read()
                    write = (cur != content)
                except Exception:
                    write = True
            if write:
                with open(out_path, 'w', encoding='utf-8') as f:
                    f.write(content)
        except Exception as e:
            raise RuntimeError(f'failed to materialize GCP credentials: {e}')
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = out_path
        return out_path
    raise RuntimeError('GOOGLE_APPLICATION_CREDENTIALS not set and no GCP_SA_JSON in keyring')

