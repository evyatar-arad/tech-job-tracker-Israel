"""Safe persistence helpers: atomic JSON writes, append-only audit log, byte-exact archives."""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
AUDIT_LOG = os.path.join(DATA_DIR, "job_updates_audit.jsonl")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path, data):
    """
    Write JSON to a temp file in the same directory, fsync, re-parse it to validate, then os.replace().
    A crash or a serialization error can never leave a truncated/corrupt file behind.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    json.loads(payload)  # validate before touching disk
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, "r", encoding="utf-8") as f:
            json.load(f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def append_audit(event_type, **fields):
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    record = {"at": now_iso(), "event": event_type, **fields}
    with open(AUDIT_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_bytes(src_path, label, raw_bytes=None):
    """
    Store an exact byte copy under data/archive/<label>.<sha12>.json (idempotent: same content -> same file).
    Returns (archive_path, sha256).
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    if raw_bytes is None:
        with open(src_path, "rb") as f:
            raw_bytes = f.read()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    dest = os.path.join(ARCHIVE_DIR, f"{label}.{digest[:12]}.json")
    if not os.path.exists(dest):
        with open(dest, "wb") as f:
            f.write(raw_bytes)
    return dest, digest
