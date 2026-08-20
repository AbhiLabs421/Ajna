"""Search tools: glob and grep, implemented on the stdlib alone.

File reads are I/O bound, so grep fans out over a small thread pool. On a
few-thousand-file repository this stays comfortably under a second.
"""

import fnmatch
import os
import re

from . import SKIP_DIRS, Result, register
from .. import ui

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_MATCHES = 300
WORKERS = 8


def _walk(root, follow_links=False):
    """Yield file paths under root, pruning noisy directories."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=follow_links)
            except OSError:
                continue
            if is_dir:
                if entry.name in SKIP_DIRS or entry.name.startswith(".") and entry.name != ".github":
                    continue
                stack.append(entry.path)
            else:
                yield entry.path


def _match_glob(path, root, pattern):
    """Match a path against a glob, honouring ** and bare-name patterns."""
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if fnmatch.fnmatch(rel, pattern):
        return True
    # "**/*.py" should also match top-level "main.py"
    if pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:]):
        return True
    # A bare "*.py" should match at any depth, which is what users expect.
    if "/" not in pattern and fnmatch.fnmatch(os.path.basename(path), pattern):
        return True
    return False


@register(
    "glob",
    """
    Find files by name pattern, newest first. Supports ** for any depth,
    e.g. "**/*.py", "src/**/test_*.js", "Dockerfile".
    Use this to locate files; use grep to search inside them.
    """,
    {
        "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.ts"},
        "path": {"type": "string", "description": "Directory to search under (default: workspace root)."},
        "limit": {"type": "integer", "description": "Max results (default 200)."},
    },
    required=["pattern"],
    summary=lambda a: "glob %s" % a.get("pattern", "?"),
)
def glob(ctx, pattern, path=None, limit=200):
    root = ctx.resolve(path)
    if not os.path.isdir(root):
        return Result.fail("%s is not a directory" % ctx.rel(root))

    limit = max(1, min(int(limit or 200), 1000))
    found = []
    for filepath in _walk(root):
        if _match_glob(filepath, root, pattern):
            try:
                found.append((os.path.getmtime(filepath), filepath))
            except OSError:
                found.append((0, filepath))

    if not found:
        return Result("No files match %r under %s" % (pattern, ctx.rel(root)),
                      display=ui.c("  no matches", "grey"))

    found.sort(reverse=True)
    paths = [ctx.rel(p) for _, p in found[:limit]]
    note = ""
    if len(found) > limit:
        note = "\n(%d more not shown)" % (len(found) - limit)
    return Result("\n".join(paths) + note,
                  display=ui.c("  %d file%s" % (len(found), "" if len(found) == 1 else "s"), "grey"),
                  meta={"count": len(found)})


@register(
    "grep",
    """
    Search file contents with a regular expression. Returns matching lines
    with file:line prefixes by default. Filter the file set with `glob`.
    This is the fast way to find where something is defined or used.
    """,
    {
        "pattern": {"type": "string", "description": "Python regular expression."},
        "path": {"type": "string", "description": "File or directory to search (default: workspace root)."},
        "glob": {"type": "string", "description": "Only search files matching this pattern, e.g. *.py"},
        "mode": {"type": "string", "enum": ["content", "files", "count"],
                 "description": "content = matching lines (default), files = paths only, count = per-file counts."},
        "ignore_case": {"type": "boolean", "description": "Case-insensitive search."},
        "context": {"type": "integer", "description": "Lines of surrounding context to include."},
        "limit": {"type": "integer", "description": "Max matches to return (default 100)."},
    },
    required=["pattern"],
    summary=lambda a: "grep %s" % a.get("pattern", "?"),
)
def grep(ctx, pattern, path=None, glob=None, mode="content",
         ignore_case=False, context=0, limit=100):
    root = ctx.resolve(path)
    if not os.path.exists(root):
        return Result.fail("%s does not exist" % ctx.rel(root))

    flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
    literal = False
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        # Models routinely search for things like "total(" or "foo[0]".
        # Falling back to a literal search beats spending a turn on the error.
        try:
            rx = re.compile(re.escape(pattern), flags)
            literal = True
        except re.error:
            return Result.fail("bad regular expression: %s" % exc)

    limit = max(1, min(int(limit or 100), MAX_MATCHES))
    context = max(0, min(int(context or 0), 10))

    if os.path.isfile(root):
        candidates = [root]
    else:
        candidates = [p for p in _walk(root)
                      if not glob or _match_glob(p, root, glob)]

    if not candidates:
        return Result("No files to search under %s" % ctx.rel(root),
                      display=ui.c("  no files", "grey"))

    results = _scan(candidates, rx, context)
    note_literal = ("\n(%r is not valid regex, searched for it literally)"
                    % pattern) if literal else ""

    if not results:
        return Result("No matches for %r in %d file%s" % (
            pattern, len(candidates), "" if len(candidates) == 1 else "s"),
            display=ui.c("  no matches", "grey"))

    total = sum(len(hits) for _, hits in results)
    results.sort(key=lambda item: -len(item[1]))

    if mode == "files":
        body = "\n".join(ctx.rel(p) for p, _ in results[:limit])
        return Result("%d file(s) with matches:\n%s" % (len(results), body),
                      display=ui.c("  %d files, %d matches" % (len(results), total), "grey"),
                      meta={"count": total})

    if mode == "count":
        body = "\n".join("%s: %d" % (ctx.rel(p), len(h)) for p, h in results[:limit])
        return Result("%d total matches\n%s" % (total, body),
                      display=ui.c("  %d matches" % total, "grey"),
                      meta={"count": total})

    lines, shown = [], 0
    for filepath, hits in results:
        if shown >= limit:
            break
        rel = ctx.rel(filepath)
        for lineno, text in hits:
            if shown >= limit:
                break
            lines.append("%s:%d: %s" % (rel, lineno, text[:400]))
            shown += 1

    note = "\n\n(%d more matches not shown)" % (total - shown) if total > shown else ""
    note += note_literal
    return Result("\n".join(lines) + note,
                  display=ui.c("  %d matches in %d files" % (total, len(results)), "grey"),
                  meta={"count": total})


def _scan(paths, rx, context):
    """Search many files concurrently; returns [(path, [(lineno, text)])]."""
    from concurrent.futures import ThreadPoolExecutor

    def scan_one(filepath):
        try:
            if os.path.getsize(filepath) > MAX_FILE_BYTES:
                return None
            with open(filepath, "rb") as fh:
                blob = fh.read()
        except OSError:
            return None
        if b"\x00" in blob[:4096]:
            return None
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            text = blob.decode("latin-1", "replace")

        hits = []
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if rx.search(line):
                if context:
                    lo = max(0, i - context)
                    hi = min(len(lines), i + context + 1)
                    for j in range(lo, hi):
                        hits.append((j + 1, lines[j]))
                else:
                    hits.append((i + 1, line))
                if len(hits) > MAX_MATCHES:
                    break
        return (filepath, hits) if hits else None

    out = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for item in pool.map(scan_one, paths):
            if item:
                out.append(item)
    return out
