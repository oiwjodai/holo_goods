from __future__ import annotations
import os


def ensure_gcp_credentials_path() -> str:
    """Return a readable GOOGLE_APPLICATION_CREDENTIALS path from environment."""
    path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '').strip()
    if path and os.path.exists(path):
        return path
    raise RuntimeError('GOOGLE_APPLICATION_CREDENTIALS is not set or points to a missing file')

