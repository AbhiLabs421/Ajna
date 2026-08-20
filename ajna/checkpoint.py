"""Undo for file changes.

Before any tool writes to a file, the file's current bytes are copied aside.
`/undo` restores everything one turn touched — which is the unit a user
actually thinks in ("that last thing you did was wrong"), not the individual
edit.

Snapshots are per session and pruned with it. Nothing here touches git, so it
works in a directory that is not a repository, and it never interferes with
staged work.
"""

import os
import shutil
import time

from .config import HOME_DIR

CHECKPOINT_DIR = os.path.join(HOME_DIR, "checkpoints")
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


class Checkpoint:
    def __init__(self, session_id, root):
        self.session_id = session_id
        self.root = os.path.abspath(root)
        self.dir = os.path.join(CHECKPOINT_DIR, session_id)
        self.entries = []      # {turn, path, blob, existed, label}
        self.turn = 0
        self._seen = set()     # (turn, path) already snapshotted
        self._counter = 0
        self.enabled = True

    # -- recording ------------------------------------------------------

    def begin_turn(self, label=""):
        self.turn += 1
        self.label = label
        return self.turn

    def record(self, path, label=""):
        """Snapshot `path` as it is now. Safe to call repeatedly."""
        if not self.enabled:
            return
        path = os.path.abspath(path)
        key = (self.turn, path)
        if key in self._seen:
            return          # the state before the turn is what we want to keep
        self._seen.add(key)

        existed = os.path.isfile(path)
        blob = None
        if existed:
            try:
                if os.path.getsize(path) > MAX_SNAPSHOT_BYTES:
                    return  # too large to be worth it; skip rather than stall
                os.makedirs(self.dir, exist_ok=True)
                self._counter += 1
                blob = os.path.join(self.dir, "%04d.blob" % self._counter)
                shutil.copyfile(path, blob)
            except OSError:
                return

        self.entries.append({
            "turn": self.turn, "path": path, "blob": blob,
            "existed": existed, "label": label or getattr(self, "label", ""),
            "at": time.time(),
        })

    # -- inspection -----------------------------------------------------

    def turns(self):
        """Turn numbers that changed something, most recent first."""
        return sorted({e["turn"] for e in self.entries}, reverse=True)

    def summary(self, turn):
        touched = [e for e in self.entries if e["turn"] == turn]
        created = [e for e in touched if not e["existed"]]
        changed = [e for e in touched if e["existed"]]
        return {
            "turn": turn,
            "label": touched[0]["label"] if touched else "",
            "created": [self._rel(e["path"]) for e in created],
            "changed": [self._rel(e["path"]) for e in changed],
        }

    def _rel(self, path):
        try:
            rel = os.path.relpath(path, self.root)
            return rel if not rel.startswith("..") else path
        except ValueError:
            return path

    # -- undo -----------------------------------------------------------

    def undo(self, turn=None):
        """Restore one turn's files. Returns (restored, deleted, failed)."""
        available = self.turns()
        if not available:
            return [], [], []
        target = turn if turn is not None else available[0]

        touched = [e for e in self.entries if e["turn"] == target]
        restored, deleted, failed = [], [], []

        # Reverse order so a file created then edited ends up gone, not stale.
        for entry in reversed(touched):
            path, blob, existed = entry["path"], entry["blob"], entry["existed"]
            try:
                if existed and blob and os.path.exists(blob):
                    shutil.copyfile(blob, path)
                    restored.append(self._rel(path))
                elif not existed:
                    if os.path.exists(path):
                        os.remove(path)
                        deleted.append(self._rel(path))
                else:
                    failed.append(self._rel(path))
            except OSError as exc:
                failed.append("%s (%s)" % (self._rel(path), exc))

        self.entries = [e for e in self.entries if e["turn"] != target]
        self._seen = {(e["turn"], e["path"]) for e in self.entries}
        return restored, deleted, failed

    # -- housekeeping ---------------------------------------------------

    def discard(self):
        try:
            shutil.rmtree(self.dir, ignore_errors=True)
        except OSError:
            pass
        self.entries = []
        self._seen = set()


def prune(keep=20):
    """Drop the oldest checkpoint directories."""
    try:
        dirs = [os.path.join(CHECKPOINT_DIR, n)
                for n in os.listdir(CHECKPOINT_DIR)]
    except OSError:
        return
    dirs = [d for d in dirs if os.path.isdir(d)]
    if len(dirs) <= keep:
        return
    dirs.sort(key=lambda d: os.path.getmtime(d))
    for path in dirs[:len(dirs) - keep]:
        shutil.rmtree(path, ignore_errors=True)
