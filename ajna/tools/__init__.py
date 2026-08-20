"""Tool registry.

A tool is a plain function plus a JSON schema. Registration order here is the
order the model sees, which matters: the cheap read-only tools come first so
the model reaches for them before the expensive ones.
"""

import os

REGISTRY = {}
ORDER = []

# Directories never worth walking into. Lives here rather than in files.py so
# that importing it does not drag a tool module in as a side effect.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    ".tox", "coverage", ".gradle", "vendor", ".terraform",
}


class Result:
    """What a tool hands back.

    `content` goes to the model. `display` is what the human sees; when it is
    None the caller prints a one-line summary instead of the full payload, so
    a 2000-line file read does not flood the terminal.
    """

    __slots__ = ("content", "display", "error", "meta")

    def __init__(self, content, display=None, error=False, meta=None):
        self.content = content if isinstance(content, str) else str(content)
        self.display = display
        self.error = error
        self.meta = meta or {}

    @classmethod
    def fail(cls, message):
        return cls("Error: " + message, error=True)


class Tool:
    __slots__ = ("name", "description", "parameters", "fn",
                 "concurrent", "mutating", "summary")

    def __init__(self, name, description, parameters, fn,
                 concurrent=True, mutating=False, summary=None):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn
        self.concurrent = concurrent    # safe to run alongside other tools
        self.mutating = mutating        # changes disk or runs commands
        self.summary = summary          # (args) -> short human label

    def schema(self):
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}

    def label(self, args):
        if self.summary:
            try:
                return self.summary(args)
            except Exception:
                pass
        return self.name


def register(name, description, properties, required=(),
             concurrent=True, mutating=False, summary=None):
    """Decorator that adds a function to the registry."""

    def deco(fn):
        params = {"type": "object", "properties": properties,
                  "required": list(required)}
        tool = Tool(name, description.strip(), params, fn,
                    concurrent=concurrent, mutating=mutating, summary=summary)
        REGISTRY[name] = tool
        ORDER.append(name)
        return fn

    return deco


class Context:
    """Ambient state tools need: where we are, and what has been seen."""

    def __init__(self, root, cfg, permit=None, checkpoint=None, depth=0):
        self.root = os.path.abspath(root)
        self.cfg = cfg
        self.permit = permit or (lambda *a, **k: True)
        self.read_files = {}     # abs path -> mtime at time of read
        self.todos = []
        self.checkpoint = checkpoint    # records file state before mutations
        self.depth = depth              # 0 for the top-level agent
        self.spawn = None               # set by the agent that owns this context
        self.removed_names = set()      # definitions deleted by edits this turn

    def child(self):
        """A context for a sub-agent: same workspace, its own read tracking."""
        sub = Context(self.root, self.cfg, permit=self.permit,
                      checkpoint=self.checkpoint, depth=self.depth + 1)
        sub.spawn = self.spawn
        return sub

    def resolve(self, path):
        """Make a tool-supplied path absolute, relative to the workspace root."""
        if not path:
            return self.root
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.root, path)
        return os.path.normpath(path)

    def rel(self, path):
        try:
            rel = os.path.relpath(path, self.root)
            return rel if not rel.startswith("..") else path
        except ValueError:
            return path

    def inside(self, path):
        try:
            return os.path.commonpath([os.path.abspath(path), self.root]) == self.root
        except ValueError:
            return False


_loaded = False


def load():
    """Import the tool modules. Called once, lazily, from the agent.

    The guard is an explicit flag rather than `if REGISTRY`: importing a single
    tool module elsewhere (prompt.py wants SKIP_DIRS) would otherwise make the
    registry look populated and silently skip the rest.
    """
    global _loaded
    if _loaded:
        return REGISTRY
    from . import files, search, shell, misc, agents  # noqa: F401
    _loaded = True
    return REGISTRY


def schemas():
    load()
    return [REGISTRY[n].schema() for n in ORDER]


def get(name):
    load()
    return REGISTRY.get(name)
