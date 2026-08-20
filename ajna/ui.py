"""Terminal presentation: colour, spinner, streaming markdown, diffs.

Everything degrades to plain text when the stream is not a TTY, so piping
`ajna -p "..."` into another program stays clean.
"""

import os
import re
import shutil
import sys
import threading
import time

_ENABLED = True


def _enable_windows_ansi():
    """Turn on virtual-terminal processing so ANSI works in older consoles."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle in (-11, -12):  # stdout, stderr
            mode = ctypes.c_uint32()
            h = kernel32.GetStdHandle(handle)
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004)
        return True
    except Exception:
        return False


def _force_utf8():
    """Make stdout/stderr able to carry the glyphs this module prints.

    Redirect ajna's output to a file or a pipe on Windows and Python picks
    cp1252, where a bullet is unencodable and every status line raises
    UnicodeEncodeError. Reconfiguring is the fix; _write below is the net
    underneath it for streams that cannot be reconfigured.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


# Fallbacks for a stream that still cannot encode them.
_PLAIN = {
    "●": "*", "○": "o", "◐": ">", "•": "-", "─": "-", "│": "|",
    "✗": "x", "✓": "v", "✎": "*", "←": "<-", "…": "...", "›": ">",
    "·": ".", "⠋": "-", "⠙": "\\", "⠹": "|", "⠸": "/", "⠼": "-",
    "⠴": "\\", "⠦": "|", "⠧": "/", "⠇": "-", "⠏": "\\",
}


def _asciify(text):
    for fancy, plain in _PLAIN.items():
        text = text.replace(fancy, plain)
    return text.encode("ascii", "replace").decode("ascii")


def _write(stream, text):
    try:
        stream.write(text)
    except UnicodeEncodeError:
        stream.write(_asciify(text))


def init(color="auto"):
    global _ENABLED
    _force_utf8()
    if color == "never" or os.environ.get("NO_COLOR"):
        _ENABLED = False
    elif color == "always":
        _ENABLED = True
        _enable_windows_ansi()
    else:
        _ENABLED = sys.stdout.isatty() and _enable_windows_ansi()
    return _ENABLED


def enabled():
    return _ENABLED


# --- colour -----------------------------------------------------------------

_CODES = {
    "reset": "0", "bold": "1", "dim": "2", "italic": "3", "under": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "grey": "90",
    "bred": "91", "bgreen": "92", "byellow": "93", "bblue": "94",
    "bmagenta": "95", "bcyan": "96",
}


def c(text, *styles):
    """Wrap text in ANSI styles (no-op when colour is disabled)."""
    if not _ENABLED or not styles:
        return text
    codes = ";".join(_CODES[s] for s in styles if s in _CODES)
    return "\x1b[%sm%s\x1b[0m" % (codes, text) if codes else text


def width(default=80):
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def out(text=""):
    _write(sys.stdout, text + "\n")
    sys.stdout.flush()


def raw(text):
    _write(sys.stdout, text)
    sys.stdout.flush()


def rule(label=""):
    w = min(width(), 100)
    if label:
        bar = "─" * max(0, w - len(label) - 3)
        out(c("─ " + label + " " + bar, "grey"))
    else:
        out(c("─" * w, "grey"))


def err(msg):
    _write(sys.stderr, c("✗ ", "bred") + str(msg) + "\n")
    sys.stderr.flush()


def warn(msg):
    out(c("! ", "byellow") + str(msg))


def info(msg):
    out(c("· ", "grey") + c(str(msg), "grey"))


# --- spinner ----------------------------------------------------------------

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """Single-line status indicator with an elapsed-time counter."""

    def __init__(self, label="thinking"):
        self.label = label
        self._stop = threading.Event()
        self._thread = None
        self._start = 0.0
        self._active = False

    def start(self, label=None):
        if label:
            self.label = label
        if not _ENABLED or not sys.stdout.isatty() or self._active:
            return self
        self._active = True
        self._stop.clear()
        self._start = time.time()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            elapsed = time.time() - self._start
            frame = _FRAMES[i % len(_FRAMES)]
            line = "%s %s %s" % (
                c(frame, "bcyan"), c(self.label, "grey"),
                c("(%.0fs · esc to interrupt)" % elapsed, "grey"),
            )
            raw("\r\x1b[2K" + line)
            i += 1
            self._stop.wait(0.08)
        raw("\r\x1b[2K")

    def update(self, label):
        self.label = label

    def stop(self):
        if not self._active:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        self._active = False


# --- streaming markdown -----------------------------------------------------

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")


def _inline(text):
    text = _BOLD.sub(lambda m: c(m.group(1), "bold"), text)
    text = _CODE.sub(lambda m: c(m.group(1), "bcyan"), text)
    return text


def render_line(line):
    """Style one complete markdown line for terminal output."""
    if not _ENABLED:
        return line
    m = _HEAD.match(line)
    if m:
        return c(m.group(2), "bold", "bcyan")
    m = _BULLET.match(line)
    if m:
        return "%s%s %s" % (m.group(1), c("•", "bcyan"), _inline(m.group(3)))
    return _inline(line)


class StreamRenderer:
    """Buffers streamed tokens and emits styled, complete lines.

    Markdown needs whole lines to style correctly, but users want to see
    output as it arrives. Compromise: flush a line the moment it terminates,
    and hold only the trailing partial line, which is printed unstyled so the
    text never appears to stall.

    Erasing that tail again is the subtle part. A partial line long enough to
    wrap occupies several terminal rows, and a single carriage-return erase clears only the last
    one; the rest stay on screen and the finished line prints underneath them,
    which reads as duplicated output. So the tail is previewed only while it
    fits on a single row.
    """

    def __init__(self):
        self.buf = ""
        self.in_code = False
        self._pending = ""   # unstyled tail currently on screen

    def feed(self, chunk):
        if not chunk:
            return
        self.buf += chunk
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._clear_pending()
            self._emit(line)
        if self.buf and _ENABLED and sys.stdout.isatty():
            self._clear_pending()
            if len(self.buf) < width() - 1:
                self._pending = self.buf
                raw(c(self.buf, "grey") if self.in_code else self.buf)

    def _clear_pending(self):
        if self._pending:
            raw("\r\x1b[2K")
            self._pending = ""

    def _emit(self, line):
        stripped = line.strip()
        if stripped.startswith("```"):
            self.in_code = not self.in_code
            lang = stripped[3:].strip()
            out(c("  " + (lang or "code"), "grey") if self.in_code else c("  ─", "grey"))
            return
        if self.in_code:
            out(c("  " + line, "bgreen"))
        else:
            out(render_line(line))

    def close(self):
        if self.buf:
            self._clear_pending()
            self._emit(self.buf)
            self.buf = ""
        self.in_code = False


# --- diffs ------------------------------------------------------------------

def diff(old, new, path="", context=3):
    """Coloured unified diff, trimmed to a readable size."""
    import difflib

    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=path, tofile=path, lineterm="", n=context,
    ))
    if not lines:
        return c("  (no textual change)", "grey")

    shown, added, removed = [], 0, 0
    for line in lines[2:]:
        if line.startswith("+"):
            added += 1
            if len(shown) < 60:
                shown.append(c("  + " + line[1:], "green"))
        elif line.startswith("-"):
            removed += 1
            if len(shown) < 60:
                shown.append(c("  - " + line[1:], "red"))
        elif line.startswith("@@"):
            if len(shown) < 60:
                shown.append(c("  " + line, "grey"))
        elif len(shown) < 60:
            shown.append(c("    " + line[1:], "grey"))

    tail = ""
    if added + removed > 60:
        tail = c("\n  … %d more changed lines" % (added + removed - 60), "grey")
    summary = c("  %d added, %d removed" % (added, removed), "grey")
    return "\n".join(shown) + tail + "\n" + summary


def confirm(prompt, default=False):
    """Yes/no prompt that treats a closed stdin as the default."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        ans = input(c("? ", "byellow") + prompt + c(suffix, "grey")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        out("")
        return False
    if not ans:
        return default
    return ans in ("y", "yes")
