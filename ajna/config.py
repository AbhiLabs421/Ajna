"""Configuration: layered resolution from defaults -> file -> env -> CLI flags."""

import json
import os

HOME_DIR = os.path.join(os.path.expanduser("~"), ".ajna")
CONFIG_PATH = os.path.join(HOME_DIR, "config.json")
SESSION_DIR = os.path.join(HOME_DIR, "sessions")

# Provider presets. `kind` selects the wire protocol implementation.
PROVIDERS = {
    "ollama": {
        "kind": "ollama",
        "base_url": "https://ollama.com",
        "key_env": "OLLAMA_API_KEY",
        "default_model": "gpt-oss:120b",
    },
    "ollama-local": {
        "kind": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "key_env": "OLLAMA_API_KEY",
        "default_model": "qwen3:8b",
    },
    "openai": {
        "kind": "openai",
        "base_url": "https://api.openai.com",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "anthropic": {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-5",
    },
    "groq": {
        "kind": "openai",
        "base_url": "https://api.groq.com/openai",
        "key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "together": {
        "kind": "openai",
        "base_url": "https://api.together.xyz",
        "key_env": "TOGETHER_API_KEY",
        "default_model": "Qwen/Qwen2.5-Coder-32B-Instruct",
    },
    "openrouter": {
        "kind": "openai",
        "base_url": "https://openrouter.ai/api",
        "key_env": "OPENROUTER_API_KEY",
        "default_model": "qwen/qwen-2.5-coder-32b-instruct",
    },
}

DEFAULTS = {
    "provider": "ollama",
    "base_url": "",          # blank -> take from provider preset
    "api_key": "",
    "model": "",             # blank -> take from provider preset
    "small_model": "gpt-oss:20b",   # used for compaction / titles
    "temperature": 0.3,
    "max_tokens": 8192,
    "context_tokens": 120000,
    "compact_at": 0.75,      # fraction of context before auto-compaction
    "stream": True,
    "max_steps": 60,         # tool-loop safety valve
    "timeout": 300,
    "color": "auto",         # auto | always | never
    "auto_approve": [],      # persisted permission rules, e.g. "shell:git *"
    "yolo": False,           # skip all permission prompts
    "mcp_servers": {},       # name -> {command, args, env, cwd, disabled}
    "checkpoints": True,     # snapshot files before changing them, for /undo
    "shell": "",             # "" = ask once, then powershell | cmd | bash | sh | auto
}


class Config(dict):
    """A dict with attribute access and layered loading."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    @property
    def kind(self):
        return PROVIDERS.get(self["provider"], PROVIDERS["ollama"])["kind"]

    def save(self):
        os.makedirs(HOME_DIR, exist_ok=True)
        # Only persist keys that differ from defaults, keeps the file readable.
        out = {k: v for k, v in self.items() if k in DEFAULTS and v != DEFAULTS[k]}
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass  # best effort; Windows ACLs differ


def load(overrides=None):
    """Resolve configuration. Precedence: CLI > env > config file > preset."""
    cfg = Config(DEFAULTS)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except (OSError, ValueError):
            pass  # a corrupt config should never block startup

    for key in DEFAULTS:
        env = os.environ.get("AJNA_" + key.upper())
        if env is not None:
            cfg[key] = _coerce(DEFAULTS[key], env)

    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})

    preset = PROVIDERS.get(cfg["provider"], PROVIDERS["ollama"])
    if not cfg["base_url"]:
        cfg["base_url"] = preset["base_url"]
    if not cfg["model"]:
        cfg["model"] = preset["default_model"]
    if not cfg["api_key"]:
        cfg["api_key"] = os.environ.get(preset["key_env"], "")
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    return cfg


def _coerce(template, raw):
    """Cast an env-var string to the type of its default."""
    if isinstance(template, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(template, int):
        try:
            return int(raw)
        except ValueError:
            return template
    if isinstance(template, float):
        try:
            return float(raw)
        except ValueError:
            return template
    if isinstance(template, list):
        return [p for p in raw.split(",") if p]
    return raw
