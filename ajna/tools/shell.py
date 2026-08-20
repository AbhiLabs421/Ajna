"""Shell tool.

Commands are the one place an agent can do real damage, so this module keeps
two guardrails: a hard blocklist that never runs, and a permission callback
for everything else that is not a known read-only command.
"""

import os
import re
import shutil
import subprocess

from . import Result, register
from .. import ui

MAX_OUTPUT = 30000
DEFAULT_TIMEOUT = 120

# Each entry: candidate executables, the flags that mean "run this string",
# and the syntax note the model is told about.
SHELL_SPECS = {
    "powershell": (
        ("pwsh", "powershell.exe", "powershell"),
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"],
        "PowerShell. Use cmdlets (Get-ChildItem, Test-Path), $env:VAR for "
        "environment variables, and `;` to chain. Note that `&&` and `||` do "
        "not work in Windows PowerShell 5.1.",
    ),
    "cmd": (
        ("cmd.exe", "cmd"),
        ["/d", "/s", "/c"],
        "Windows cmd.exe. Use `dir`, `type`, `copy`, `set VAR=value`, `%VAR%`, "
        "and `&&` to chain. PowerShell cmdlets and POSIX syntax will not work.",
    ),
    "bash": (
        ("bash", "bash.exe"),
        ["-lc"],
        "bash. Use POSIX syntax: `ls`, `cat`, `grep`, `$VAR`, `&&`, pipes. "
        "On Windows this is Git Bash, so use forward slashes in paths.",
    ),
    "sh": (
        ("sh", "dash", "sh.exe"),
        ["-c"],
        "POSIX sh. Keep to portable syntax; bashisms may not work.",
    ),
    "zsh": (("zsh",), ["-c"], "zsh. POSIX syntax with zsh extensions."),
}

CHOICES = ("auto", "powershell", "cmd", "bash", "sh", "zsh")


def _locate(candidates):
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def default_kind():
    """What `auto` resolves to on this machine."""
    if os.name == "nt":
        return "powershell"
    current = os.path.basename(os.environ.get("SHELL", "") or "")
    return current if current in SHELL_SPECS else "sh"


def available():
    """Shells actually installed here, in a sensible offer order."""
    order = ("powershell", "cmd", "bash", "zsh", "sh")
    return [k for k in order if _locate(SHELL_SPECS[k][0])]


def resolve(kind):
    """Return (kind, executable, flags, syntax_note) or (kind, None, ...)."""
    kind = (kind or "auto").strip().lower()
    if kind in ("", "auto", "all", "default"):
        kind = default_kind()
    if kind not in SHELL_SPECS:
        kind = default_kind()
    candidates, flags, note = SHELL_SPECS[kind]
    return kind, _locate(candidates), flags, note


def describe(kind):
    """One line naming the shell in force, for the system prompt."""
    kind, executable, _flags, note = resolve(kind)
    if not executable:
        return "%s (NOT INSTALLED — the shell tool will fail)" % kind
    return "%s — %s" % (kind, note)

# Commands that are never worth the risk of a mistaken match.
BLOCKED = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", re.I), "recursive/forced delete"),
    (re.compile(r"\b(mkfs|fdisk|diskpart)\b", re.I), "disk formatting"),
    (re.compile(r"\bdd\s+.*of=/dev/", re.I), "raw device write"),
    (re.compile(r":\(\)\s*\{.*\|.*&\s*\}\s*;", re.S), "fork bomb"),
    (re.compile(r"\bRemove-Item\b.*-Recurse.*-Force", re.I), "recursive/forced delete"),
    (re.compile(r"\bFormat-Volume\b", re.I), "disk formatting"),
    (re.compile(r"\bcurl\b[^|;]*\|\s*(ba)?sh", re.I), "piping a download into a shell"),
    (re.compile(r"\biwr\b[^|;]*\|\s*iex", re.I), "piping a download into a shell"),
    (re.compile(r"\bgit\s+push\b.*--force", re.I), "force push"),
    (re.compile(r"\bshutdown\b|\bReboot-Computer\b", re.I), "shutting the machine down"),
]

# Read-only commands that do not need a prompt every time.
SAFE_PREFIXES = (
    "git status", "git log", "git diff", "git branch", "git show", "git remote",
    "ls", "dir", "pwd", "cat", "type", "echo", "which", "where", "whoami",
    "node --version", "python --version", "py --version", "npm ls", "pip list",
    "git rev-parse", "git config --get", "date", "hostname", "uname",
    "Get-ChildItem", "Get-Location", "Get-Content", "Test-Path",
)


def _shell_command(command, kind):
    """Return the argv that runs `command` in the chosen shell."""
    kind, executable, flags, _note = resolve(kind)
    if not executable:
        return None, kind
    return [executable] + flags + [command], kind


def is_safe(command):
    stripped = command.strip()
    return any(stripped.lower().startswith(p.lower()) for p in SAFE_PREFIXES)


def blocked_reason(command):
    for pattern, reason in BLOCKED:
        if pattern.search(command):
            return reason
    return None


@register(
    "shell",
    """
    Run a shell command in the workspace and return its output.
    Which shell is in force is stated in the Environment section — write your
    command in that shell's syntax, not another's.
    Use it for builds, tests, git, and package managers. Prefer the dedicated
    read/edit/grep/glob tools for file work — they are faster and safer.
    Never use it for interactive commands; they will time out.
    """,
    {
        "command": {"type": "string", "description": "The command line to run."},
        "cwd": {"type": "string", "description": "Directory to run in (default: workspace root)."},
        "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120, max 600)."},
        "why": {"type": "string", "description": "One short line explaining the purpose, shown to the user."},
    },
    required=["command"],
    concurrent=False,
    mutating=True,
    summary=lambda a: "shell %s" % (a.get("command", "?")[:60]),
)
def shell(ctx, command, cwd=None, timeout=None, why=None):
    command = (command or "").strip()
    if not command:
        return Result.fail("empty command")

    reason = blocked_reason(command)
    if reason:
        return Result.fail(
            "refusing to run this command (%s). If it is genuinely intended, "
            "run it yourself." % reason)

    workdir = ctx.resolve(cwd) if cwd else ctx.root
    if not os.path.isdir(workdir):
        return Result.fail("%s is not a directory" % ctx.rel(workdir))

    if not is_safe(command):
        preview = ui.c("  $ " + command, "byellow")
        if why:
            preview += "\n" + ui.c("  " + why, "grey")
        if not ctx.permit("shell", command, preview):
            return Result.fail("user declined to run: %s" % command)

    argv, kind = _shell_command(command, ctx.cfg.get("shell"))
    if argv is None:
        return Result.fail(
            "the configured shell (%s) is not installed on this machine. "
            "Run /shell to pick another." % kind)

    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), 600))
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["NO_COLOR"] = "1"

    try:
        proc = subprocess.run(
            argv,
            cwd=workdir, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return Result.fail("command timed out after %ds: %s" % (timeout, command))
    except FileNotFoundError as exc:
        return Result.fail("cannot start shell: %s" % exc)
    except OSError as exc:
        return Result.fail("failed to run command: %s" % exc)

    output = (proc.stdout or b"").decode("utf-8", "replace").replace("\r\n", "\n").strip()
    truncated = ""
    if len(output) > MAX_OUTPUT:
        head = output[: MAX_OUTPUT // 2]
        tail = output[-MAX_OUTPUT // 2:]
        output = head + "\n\n… [%d characters trimmed] …\n\n" % (
            len(output) - MAX_OUTPUT) + tail
        truncated = " (trimmed)"

    status = "exit %d" % proc.returncode
    content = "$ %s\n%s\n%s" % (command, status, output or "(no output)")

    lines = output.splitlines()
    shown = lines[:20]
    display = "\n".join(ui.c("  " + line[:200], "grey") for line in shown)
    if len(lines) > 20:
        display += ui.c("\n  … %d more lines" % (len(lines) - 20), "grey")
    if proc.returncode != 0:
        display = (ui.c("  exit %d%s" % (proc.returncode, truncated), "bred")
                   + ("\n" + display if display else ""))

    return Result(content, display=display or ui.c("  " + status, "grey"),
                  error=proc.returncode != 0,
                  meta={"code": proc.returncode})
