"""Compensating for weaker models.

A frontier model calls the tools it was given, with the argument names it was
given. Smaller models drift: they invent `exec` because every other agent they
saw during training had one, they pass `file_path` where the schema says
`path`, and they paste diff markers into replacement text.

None of that is a reason to fail a turn. Each function here turns a class of
model mistake into either a silent correction or a specific, actionable error
message — which is worth far more to a small model than "invalid arguments".
"""

import json
import os
import re

# Names small models reach for, mapped to the tool that actually exists.
TOOL_ALIASES = {
    "exec": "shell", "bash": "shell", "sh": "shell", "run": "shell",
    "run_command": "shell", "execute": "shell", "execute_command": "shell",
    "terminal": "shell", "command": "shell", "shell_command": "shell",
    "powershell": "shell", "cmd": "shell",

    "read_file": "read", "readfile": "read", "cat": "read", "open": "read",
    "view": "read", "view_file": "read", "get_file": "read", "open_file": "read",

    "write_file": "write", "writefile": "write", "create_file": "write",
    "save": "write", "save_file": "write", "put_file": "write", "new_file": "write",

    "str_replace": "edit", "replace": "edit", "replace_in_file": "edit",
    "edit_file": "edit", "apply_patch": "edit", "patch": "edit",
    "search_replace": "edit", "modify": "edit", "update_file": "edit",

    "list_dir": "ls", "list_directory": "ls", "listdir": "ls", "dir": "ls",
    "list_files": "ls", "tree": "ls",

    "find": "glob", "find_files": "glob", "file_search": "glob",
    "search_files": "glob", "glob_files": "glob",

    "search": "grep", "ripgrep": "grep", "rg": "grep", "grep_search": "grep",
    "code_search": "grep", "text_search": "grep", "search_code": "grep",

    "todo_write": "todo", "update_todo": "todo", "plan": "todo",
    "task_list": "todo", "todos": "todo",

    "web_fetch": "fetch", "fetch_url": "fetch", "http_get": "fetch",
    "browse": "fetch", "curl": "fetch", "read_url": "fetch",

    "task": "agent", "subagent": "agent", "spawn_agent": "agent",
    "delegate": "agent",
}

# Argument names that mean the same thing as the schema's.
ARG_ALIASES = {
    "path": ("file_path", "filepath", "file", "filename", "file_name",
             "target_file", "relative_path", "dir", "directory", "folder"),
    "content": ("contents", "text", "data", "body", "file_text", "new_content"),
    "old": ("old_string", "old_str", "search", "find", "from", "original",
            "old_text", "target"),
    "new": ("new_string", "new_str", "replace", "replacement", "to",
            "new_text", "replace_with"),
    "command": ("cmd", "script", "shell_command", "command_line", "run"),
    "pattern": ("query", "regex", "search", "search_pattern", "q", "text"),
    "url": ("uri", "link", "address", "href"),
    "replace_all": ("all", "global", "replace_every"),
    "ignore_case": ("case_insensitive", "insensitive", "i", "nocase"),
    "items": ("todos", "tasks", "list", "plan"),
    "task": ("prompt", "instruction", "instructions", "description", "goal"),
    "limit": ("max", "max_results", "count", "n", "head_limit"),
    "offset": ("start", "start_line", "from_line", "skip"),
}


def fix_name(name, known):
    """Map an invented tool name onto a real one. Returns (name, note)."""
    if name in known:
        return name, None
    lowered = (name or "").strip().lower()
    if lowered in known:
        return lowered, "used %r, matched %r" % (name, lowered)

    # Providers sometimes namespace calls as "functions.read" or "tool-read".
    tail = re.split(r"[.:\-/]", lowered)[-1]
    if tail in known:
        return tail, "used %r, matched %r" % (name, tail)

    for candidate in (lowered, tail):
        target = TOOL_ALIASES.get(candidate)
        if target and target in known:
            return target, "called %r, routed to %r" % (name, target)
    return name, None


def fix_args(tool, args):
    """Rename near-miss arguments and coerce obvious type mistakes."""
    if not isinstance(args, dict):
        return {}, None

    schema = tool.parameters.get("properties", {})
    out = dict(args)
    notes = []

    for canonical, aliases in ARG_ALIASES.items():
        if canonical not in schema or canonical in out:
            continue
        for alias in aliases:
            if alias in out:
                out[canonical] = out.pop(alias)
                notes.append("%s→%s" % (alias, canonical))
                break

    # A lone positional-ish blob: models sometimes send {"input": "..."}.
    if not out and args:
        required = tool.parameters.get("required") or []
        if len(required) == 1 and len(args) == 1:
            out = {required[0]: list(args.values())[0]}
            notes.append("unnamed argument")

    for key, spec in schema.items():
        if key not in out:
            continue
        out[key] = _coerce(out[key], spec.get("type"))

    # Drop what the schema does not know about rather than raising TypeError.
    unknown = [k for k in out if k not in schema]
    for key in unknown:
        out.pop(key)

    return out, ", ".join(notes) if notes else None


def _coerce(value, want):
    if want == "string" and not isinstance(value, str):
        return value if value is None else (
            json.dumps(value) if isinstance(value, (dict, list)) else str(value))
    if want == "integer" and isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value
    if want == "boolean" and isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y")
    if want == "array" and isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else value
        except ValueError:
            return value
    return value


# --- edit-content guards ----------------------------------------------------

_DIFF_LINE = re.compile(r"^[+-](?![+-])", re.M)
_FENCE = re.compile(r"^\s*```", re.M)


def looks_like_diff(text):
    """Detect replacement text that is actually a diff hunk.

    Observed in the wild: a model emits `+    p.add_argument(...)` and the
    literal `+ ` lands in the source file. Cheap to catch, expensive to miss.
    """
    if not text or "\n" not in text:
        return None
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 2:
        return None
    marked = sum(1 for l in lines if _DIFF_LINE.match(l))
    if marked >= max(2, len(lines) // 2):
        return ("the replacement text looks like a diff — most lines start with "
                "'+' or '-'. Send the literal file content, without diff markers")
    if text.lstrip().startswith("@@"):
        return "the replacement text starts with a diff hunk header ('@@')"
    return None


def looks_fenced(text):
    """Markdown fences pasted into file content."""
    if text and _FENCE.search(text) and text.lstrip().startswith("```"):
        return ("the content is wrapped in a markdown code fence — send the raw "
                "file content, without ``` lines")
    return None


# --- post-write validation --------------------------------------------------

def validate_syntax(path, text):
    """Cheap syntax check for formats the stdlib can parse. None means ok."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".py", ".pyw"):
            compile(text, path, "exec")
        elif ext == ".json":
            json.loads(text)
        elif ext in (".xml", ".svg"):
            import xml.etree.ElementTree as ET
            ET.fromstring(text)
        elif ext == ".toml":
            try:
                import tomllib
            except ImportError:
                return None
            tomllib.loads(text)
    except SyntaxError as exc:
        return "%s syntax error at line %s: %s" % (ext.lstrip("."), exc.lineno, exc.msg)
    except Exception as exc:
        return "%s is not valid: %s" % (ext.lstrip("."), str(exc)[:200])
    return None


_DEFINITION = re.compile(
    r"^[ \t]*(?:export\s+)?(?:async\s+)?"
    r"(?:def|class|function|func|fn|interface|type|struct)\s+([A-Za-z_]\w*)"
    r"|^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?"
    r"(?:function\b|\([^)]*\)\s*=>)",
    re.M)


def removed_definitions(old, new):
    """Top-level names present before an edit and gone after it.

    A model told to "add a test" sometimes replaces one instead. The harness
    cannot know which is wanted, but it can say plainly what disappeared.
    """
    def names(text):
        found = set()
        for match in _DEFINITION.finditer(text or ""):
            found.add(match.group(1) or match.group(2))
        return found

    gone = names(old) - names(new)
    return sorted(n for n in gone if n)
