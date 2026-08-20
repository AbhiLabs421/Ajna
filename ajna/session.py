"""Session persistence so a conversation survives closing the terminal."""

import json
import os
import time

from .config import SESSION_DIR

MAX_SESSIONS = 100


def _slug(root):
    """A filesystem-safe token identifying a workspace."""
    safe = "".join(ch if ch.isalnum() else "-" for ch in os.path.abspath(root))
    return safe.strip("-").lower()[-60:] or "root"


def new_id(root):
    return "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), _slug(root))


def path_for(session_id):
    return os.path.join(SESSION_DIR, session_id + ".json")


def save(session_id, root, messages, meta=None):
    """Write a session atomically. Failures are non-fatal by design."""
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        payload = {
            "id": session_id,
            "root": os.path.abspath(root),
            "updated": time.time(),
            "meta": meta or {},
            "messages": messages,
        }
        target = path_for(session_id)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)
        _prune()
        return True
    except OSError:
        return False


def load(session_id):
    try:
        with open(path_for(session_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def listing(root=None, limit=20):
    """Recent sessions, newest first, optionally filtered to one workspace."""
    if not os.path.isdir(SESSION_DIR):
        return []
    out = []
    for name in os.listdir(SESSION_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSION_DIR, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if root and os.path.abspath(data.get("root", "")) != os.path.abspath(root):
            continue
        messages = data.get("messages") or []
        out.append({
            "id": data.get("id", name[:-5]),
            "root": data.get("root", ""),
            "updated": data.get("updated", 0),
            "turns": sum(1 for m in messages if m.get("role") == "user"),
            "title": _title(messages),
        })
    out.sort(key=lambda s: -s["updated"])
    return out[:limit]


def latest(root):
    items = listing(root, limit=1)
    return items[0]["id"] if items else None


def _title(messages):
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            line = " ".join(msg["content"].split())
            if line.startswith("["):
                continue
            return line[:70]
    return "(empty)"


def _prune():
    """Keep the session directory from growing without bound."""
    try:
        files = [os.path.join(SESSION_DIR, n) for n in os.listdir(SESSION_DIR)
                 if n.endswith(".json")]
    except OSError:
        return
    if len(files) <= MAX_SESSIONS:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for path in files[:len(files) - MAX_SESSIONS]:
        try:
            os.remove(path)
        except OSError:
            pass
