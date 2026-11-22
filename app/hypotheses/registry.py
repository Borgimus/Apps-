"""Append-only status history for research pre-registrations."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from app.research.replay.report import json_write

IDENTIFIER = re.compile(r"[A-Z][0-9]+[a-z]?")
NEXT = {
    "registered": {"screening"},
    "screening": {"killed", "passed_phase1", "blocked"},
    "passed_phase1": {"archive_replay", "blocked"},
    "archive_replay": {"forward", "killed", "blocked"},
    "forward": {"killed", "passed_forward", "blocked"},
    "blocked": {"screening", "archive_replay", "forward"},
}
REGISTRY = Path("evaluation/hypotheses/registry.json")


def record_id(value):
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid hypothesis ID")
    return value


def load_registry(root):
    payload = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("hypotheses"), dict):
        raise ValueError("Invalid hypothesis registry")
    for entry in payload["hypotheses"]:
        record_id(entry)
    return payload


def sha(root):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def verify_registration(root, hypothesis_id, *, code_sha=None):
    """Require identical rule text in a commit before reporting any results."""
    h = record_id(hypothesis_id)
    path = REGISTRY.parent / h / "preregistration.md"
    code_sha = code_sha or sha(root)
    committed = subprocess.check_output(["git", "show", f"{code_sha}:{path}"], cwd=root)
    if (root / path).read_bytes() != committed:
        raise ValueError(f"{h} preregistration differs from the committed rules")
    return subprocess.check_output(["git", "log", "-1", "--format=%H", code_sha, "--", str(path)],
                                   cwd=root, text=True).strip()


def transition(root, h, status, *, commit, path, reason=None, now=None):
    record_id(h)
    registry = load_registry(root)
    item = registry["hypotheses"][h]
    if status not in NEXT.get(item["status"], ()):
        raise ValueError(f"Invalid registry transition {item['status']} -> {status}")
    if status == "blocked" and not reason:
        raise ValueError("Blocked candidates must have a reason")
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Registry timestamps must be timezone aware")
    item.setdefault("transitions", []).append({"status": status, "at": at.astimezone(timezone.utc).isoformat(),
                                                   "commit": commit, "path": str(path), "reason": reason})
    item.update(status=status, report_path=str(path), reason=reason)
    json_write(root / REGISTRY, registry)


def register(root, h, name):
    h = record_id(h)
    code = sha(root)
    prereg_commit = verify_registration(root, h, code_sha=code)
    registry = load_registry(root)
    if h in registry["hypotheses"]:
        raise ValueError("Hypothesis IDs are never recycled")
    registry["hypotheses"][h] = {
        "name": name, "status": "registered",
        "preregistration": str(REGISTRY.parent / h / "preregistration.md"),
        "transitions": [{"status": "registered", "at": datetime.now(timezone.utc).isoformat(),
                         "commit": prereg_commit, "path": str(REGISTRY.parent / h / "preregistration.md")}],
    }
    json_write(root / REGISTRY, registry)
