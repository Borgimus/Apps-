"""Guard against committed secrets and against logging credentials.

These are cheap static checks: the tracked tree must not contain a real .env, and the
source must not print/log obvious secret material.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = [
    re.compile(r"AK[A-Z0-9]{16,}"),                 # AWS-style access key ids
    re.compile(r"sk-[A-Za-z0-9]{20,}"),             # generic secret keys
    re.compile(r"-----BEGIN (RSA|OPENSSH|EC) PRIVATE KEY-----"),
]


def _tracked_text_files():
    for p in (ROOT / "src").rglob("*.py"):
        yield p
    for p in (ROOT / "config").rglob("*.yaml"):
        yield p


def test_no_real_env_file_committed():
    # .env must never be committed; only .env.example with placeholders.
    assert not (ROOT / ".env").exists(), ".env must not exist in the tree"


def test_no_hardcoded_secrets_in_source():
    offenders = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                offenders.append((str(path), pat.pattern))
    assert not offenders, f"possible secret material found: {offenders}"


def test_source_does_not_log_credentials():
    # No source line should log/print a secret/authorization/api-key variable value.
    bad = re.compile(r"(print|log(ger)?\.\w+)\(.*(secret_key|api_secret|authorization|password)",
                     re.IGNORECASE)
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line):
                offenders.append(f"{path}:{i}")
    assert not offenders, f"credential logging detected: {offenders}"
