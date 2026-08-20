"""File tools: read, write, edit, ls."""

import os

from . import SKIP_DIRS, Result, register
from .. import repair, ui

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_LINE = 2000
DEFAULT_LIMIT = 2000
BOM = "﻿"


class Style:
    """A file's byte-level conventions, so a rewrite can preserve them."""

    __slots__ = ("newline", "bom")

    def __init__(self, newline="\n", bom=False):
        self.newline = newline
        self.bom = bom


def _read_text(path):
    """Read a file as text, returning (text, style, error).

    Newlines are normalised to \\n and any BOM is stripped, so tools compare
    and edit clean text; `style` carries what is needed to write it back
    unchanged. Skipping this normalisation is how editors corrupt CRLF files.
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, Style(), str(exc)
    if size > MAX_READ_BYTES:
        return None, Style(), (
            "file is %.1f MB, too large to read in full (use grep, or offset/limit)"
            % (size / 1e6))
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        return None, Style(), str(exc)
    if b"\x00" in blob[:8192]:
        return None, Style(), "binary file"

    text = None
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            text = blob.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = blob.decode("utf-8", "replace")

    style = Style(newline="\r\n" if "\r\n" in text else "\n",
                  bom=text.startswith(BOM))
    if style.bom:
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n"), style, None


def _write_text(path, text, style):
    """Write normalised text back in the file's original conventions."""
    data = text.replace("\n", style.newline).encode("utf-8")
    if style.bom:
        data = BOM.encode("utf-8") + data
    with open(path, "wb") as fh:
        fh.write(data)


# --- read -------------------------------------------------------------------

@register(
    "read",
    """
    Read a text file from disk. Returns line-numbered content.
    Use offset/limit for large files. Always read a file before editing it.
    """,
    {
        "path": {"type": "string", "description": "File path, absolute or relative to the workspace root."},
        "offset": {"type": "integer", "description": "1-based line to start from."},
        "limit": {"type": "integer", "description": "Maximum number of lines to return (default 2000)."},
    },
    required=["path"],
    summary=lambda a: "read %s" % a.get("path", "?"),
)
def read(ctx, path, offset=None, limit=None):
    target = ctx.resolve(path)
    if os.path.isdir(target):
        return Result.fail("%s is a directory, use ls" % ctx.rel(target))
    if not os.path.exists(target):
        return Result.fail("%s does not exist" % ctx.rel(target))

    text, _style, error = _read_text(target)
    if error:
        return Result.fail("cannot read %s: %s" % (ctx.rel(target), error))

    try:
        ctx.read_files[target] = os.path.getmtime(target)
    except OSError:
        pass

    if not text.strip():
        return Result("(file is empty)", display=ui.c("  empty file", "grey"))

    lines = text.splitlines()
    total = len(lines)
    start = max(0, (offset or 1) - 1)
    count = limit or DEFAULT_LIMIT
    window = lines[start:start + count]

    body = []
    for i, line in enumerate(window, start + 1):
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE] + " … (truncated)"
        body.append("%6d\t%s" % (i, line))

    note = ""
    end = start + len(window)
    if end < total:
        note = "\n\n(showing lines %d-%d of %d; use offset=%d for more)" % (
            start + 1, end, total, end + 1)

    return Result(
        "\n".join(body) + note,
        display=ui.c("  %d lines" % len(window), "grey"),
        meta={"lines": total},
    )


# --- write ------------------------------------------------------------------

@register(
    "write",
    """
    Write a complete file, creating parent directories as needed. Overwrites
    existing content, so read the file first when it already exists. Prefer
    `edit` for changing part of a file.
    """,
    {
        "path": {"type": "string", "description": "File path to write."},
        "content": {"type": "string", "description": "Full file content."},
    },
    required=["path", "content"],
    concurrent=False,
    mutating=True,
    summary=lambda a: "write %s" % a.get("path", "?"),
)
def write(ctx, path, content):
    target = ctx.resolve(path)
    exists = os.path.exists(target)

    old = ""
    style = Style(newline=os.linesep)
    if exists:
        if os.path.isdir(target):
            return Result.fail("%s is a directory" % ctx.rel(target))
        if target not in ctx.read_files:
            return Result.fail(
                "%s already exists and has not been read in this session. "
                "Read it first so the rewrite is deliberate." % ctx.rel(target))
        old, style, error = _read_text(target)
        if error:
            old, style = "", Style(newline=os.linesep)

    content = content.replace("\r\n", "\n")
    complaint = repair.looks_fenced(content) or repair.looks_like_diff(content)
    if complaint:
        return Result.fail(complaint + ". Nothing was written.")

    preview = ui.diff(old, content, ctx.rel(target)) if exists else _new_file_preview(content)
    if not ctx.permit("write", ctx.rel(target), preview):
        return Result.fail("user declined the write to %s" % ctx.rel(target))

    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            return Result.fail("cannot create %s: %s" % (parent, exc))

    if ctx.checkpoint:
        ctx.checkpoint.record(target, "write")

    try:
        _write_text(target, content, style)
    except OSError as exc:
        return Result.fail("cannot write %s: %s" % (ctx.rel(target), exc))

    ctx.read_files[target] = os.path.getmtime(target)
    n = content.count("\n") + 1
    verb = "Updated" if exists else "Created"
    notes, broke = _warnings(ctx, target, old, content, exists)
    summary = "%s %s, %d lines" % (verb.lower(), ctx.rel(target), n)
    return Result(
        "%s %s (%d lines)%s" % (verb, ctx.rel(target), n, notes),
        display=(ui.c("  " + summary + " - BROKEN", "bred") if broke
                 else _already_shown(ctx, preview, summary)),
        error=broke,
    )


def _already_shown(ctx, preview, summary):
    """Pick what to print after a permission prompt.

    The prompt shows the full diff before asking. Printing it again on approval
    is the same wall of text twice, which is what a long change looked like.
    """
    if getattr(ctx.permit, "showed_preview", False):
        return ui.c("  " + summary, "grey")
    return preview


def _warnings(ctx, path, old, new, existed):
    """Problems worth telling the model about immediately after a write.

    Returns (text, broke_syntax). The second value matters: a note appended to
    a successful result is read as "done, with a remark", and a small model
    ships the file anyway. Reported as a failure, the same sentence gets acted
    on. So the caller turns a newly-introduced syntax error into an error
    result -- while leaving an edit that merely fails to finish repairing an
    already-broken file as a success, since that is normal progress.
    """
    notes = []
    broke_syntax = False
    bad = repair.validate_syntax(path, new)
    if bad:
        was_broken = existed and repair.validate_syntax(path, old) is not None
        if was_broken:
            notes.append("NOTE: the file still has a %s" % bad)
        else:
            broke_syntax = True
            notes.append(
                "This change left the file unparseable: %s\n"
                "Nothing else will work until it is fixed. Read the file "
                "around that line and correct it now, before doing anything "
                "else." % bad)

    if existed:
        gone = repair.removed_definitions(old, new)
        # Remember them for the end-of-turn check, which can catch a rename
        # that was only half applied across the project.
        ctx.removed_names.update(gone)
        if gone:
            notes.append(
                "NOTE: this removed definition(s) that existed before: %s. "
                "If that was not intended, restore them." % ", ".join(gone[:8]))
            dangling = _dangling_references(ctx, path, gone)
            if dangling:
                notes.append(
                    "WARNING: %s still referenced elsewhere — %s. Update those "
                    "call sites too, or the project is now broken."
                    % ("names are" if len(dangling) > 1 else "a name is",
                       "; ".join(dangling[:6])))
    return ("\n" + "\n".join(notes)) if notes else "", broke_syntax


def _dangling_references(ctx, changed_path, names, cap=1500):
    """Find call sites still using names this edit just removed.

    This is the check a careful engineer runs after a rename and a hurried
    model skips. Doing it in the tool result makes it unskippable.
    """
    import re

    from .search import _walk

    wanted = [n for n in names if len(n) > 2][:6]
    if not wanted:
        return []
    pattern = re.compile(r"\b(%s)\b" % "|".join(re.escape(n) for n in wanted))

    hits, scanned = [], 0
    for filepath in _walk(ctx.root):
        if scanned >= cap:
            break
        if os.path.abspath(filepath) == os.path.abspath(changed_path):
            continue
        if os.path.splitext(filepath)[1] not in _CODE_EXTS:
            continue
        scanned += 1
        try:
            if os.path.getsize(filepath) > 1024 * 1024:
                continue
            with open(filepath, "rb") as fh:
                blob = fh.read()
        except OSError:
            continue
        if b"\x00" in blob[:2048]:
            continue
        text = blob.decode("utf-8", "replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            match = pattern.search(line)
            if match:
                hits.append("%s:%d uses %s" % (ctx.rel(filepath), lineno,
                                               match.group(1)))
                break
        if len(hits) >= 6:
            break
    return hits


_CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".kt", ".swift",
    ".scala", ".sh", ".sql", ".vue", ".svelte",
}


def _new_file_preview(content):
    lines = content.splitlines()[:20]
    body = "\n".join(ui.c("  + " + line, "green") for line in lines)
    if content.count("\n") + 1 > 20:
        body += ui.c("\n  … %d more lines" % (content.count("\n") + 1 - 20), "grey")
    return body


# --- edit -------------------------------------------------------------------

@register(
    "edit",
    """
    Replace an exact string in a file. `old` must appear exactly once unless
    replace_all is true, so include surrounding lines to make it unique.
    Read the file first. Whitespace and indentation must match exactly.
    """,
    {
        "path": {"type": "string", "description": "File to edit."},
        "old": {"type": "string", "description": "Exact text to find, including indentation."},
        "new": {"type": "string", "description": "Replacement text."},
        "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring a unique match."},
    },
    required=["path", "old", "new"],
    concurrent=False,
    mutating=True,
    summary=lambda a: "edit %s" % a.get("path", "?"),
)
def edit(ctx, path, old, new, replace_all=False):
    target = ctx.resolve(path)
    if not os.path.exists(target):
        return Result.fail("%s does not exist" % ctx.rel(target))
    if old == new:
        return Result.fail("old and new are identical, nothing to do")

    text, style, error = _read_text(target)
    if error:
        return Result.fail("cannot read %s: %s" % (ctx.rel(target), error))

    # The model works with normalised text, so normalise what it sends back.
    old = old.replace("\r\n", "\n")
    new = new.replace("\r\n", "\n")

    if target in ctx.read_files:
        try:
            if os.path.getmtime(target) > ctx.read_files[target] + 0.001:
                ctx.read_files[target] = os.path.getmtime(target)
                return Result.fail(
                    "%s changed on disk since it was read. Read it again before "
                    "editing." % ctx.rel(target))
        except OSError:
            pass

    hits = text.count(old)
    if hits == 0:
        hint = _near_miss(text, old)
        return Result.fail("string not found in %s.%s" % (ctx.rel(target), hint))
    if hits > 1 and not replace_all:
        return Result.fail(
            "string appears %d times in %s. Add surrounding context to make it "
            "unique, or set replace_all." % (hits, ctx.rel(target)))

    complaint = repair.looks_like_diff(new)
    if complaint:
        return Result.fail(complaint + ". Nothing was changed.")

    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    preview = ui.diff(text, updated, ctx.rel(target))
    if not ctx.permit("write", ctx.rel(target), preview):
        return Result.fail("user declined the edit to %s" % ctx.rel(target))

    if ctx.checkpoint:
        ctx.checkpoint.record(target, "edit")

    try:
        _write_text(target, updated, style)
    except OSError as exc:
        return Result.fail("cannot write %s: %s" % (ctx.rel(target), exc))

    ctx.read_files[target] = os.path.getmtime(target)
    notes, broke = _warnings(ctx, target, text, updated, True)
    return Result(
        "Edited %s (%d replacement%s)%s"
        % (ctx.rel(target), hits if replace_all else 1,
           "s" if replace_all and hits > 1 else "", notes),
        display=(ui.c("  edited " + ctx.rel(target) + " - BROKEN", "bred") if broke
                 else _already_shown(ctx, preview, "edited " + ctx.rel(target))),
        error=broke,
    )


def _near_miss(text, old):
    """Explain a failed match when the cause is likely whitespace."""
    probe = old.strip()
    if probe and probe in text:
        return " A version with different leading/trailing whitespace exists — match the file's exact indentation."
    first = old.strip().splitlines()[0] if old.strip() else ""
    if first and first in text:
        return " The first line matches but the rest does not — re-read the file, it may differ from what you expect."
    return " Re-read the file to get its exact current content."


# --- ls ---------------------------------------------------------------------

@register(
    "ls",
    """
    List a directory tree. Skips version-control and dependency folders.
    Good for orienting yourself in an unfamiliar project.
    """,
    {
        "path": {"type": "string", "description": "Directory to list (default: workspace root)."},
        "depth": {"type": "integer", "description": "How many levels to descend (default 2, max 6)."},
    },
    summary=lambda a: "ls %s" % (a.get("path") or "."),
)
def ls(ctx, path=None, depth=2):
    root = ctx.resolve(path)
    if not os.path.exists(root):
        return Result.fail("%s does not exist" % ctx.rel(root))
    if not os.path.isdir(root):
        return Result("%s (file, %d bytes)" % (ctx.rel(root), os.path.getsize(root)))

    depth = max(1, min(int(depth or 2), 6))
    lines, count = [], 0
    limit = 800

    def walk(directory, prefix, level):
        nonlocal count
        if level > depth or count >= limit:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        for entry in entries:
            if count >= limit:
                lines.append(prefix + "… (truncated)")
                return
            if entry.name.startswith(".") and entry.name not in (".github", ".env.example"):
                continue
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    lines.append(prefix + entry.name + "/ (skipped)")
                    count += 1
                    continue
                lines.append(prefix + entry.name + "/")
                count += 1
                walk(entry.path, prefix + "  ", level + 1)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append("%s%s  %s" % (prefix, entry.name, _human(size)))
                count += 1

    walk(root, "", 1)
    header = ctx.rel(root) + "/"
    body = "\n".join(lines) if lines else "(empty)"
    return Result(header + "\n" + body,
                  display=ui.c("  %d entries" % count, "grey"))


def _human(size):
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return "%d%s" % (size, unit)
        size //= 1024
    return "%dT" % size
