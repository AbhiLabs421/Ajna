"""Command-line entry point: argument parsing, the REPL, and slash commands."""

import os
import sys
import time

_T0 = time.time()

from . import __version__, config, perm, session, ui
from .agent import Agent
from .checkpoint import Checkpoint
from .provider import Client, ProviderError
from .tools import Context

USAGE = """\
ajna — a fast terminal coding agent

usage:
  ajna                       start an interactive session
  ajna "fix the failing test"
  ajna -p "what does main.py do?"     one-shot, prints and exits
  echo "..." | ajna -p                read the prompt from stdin

options:
  -p, --print            non-interactive; print the answer and exit
  -m, --model MODEL      model to use for this run
      --provider NAME    ollama | ollama-local | openai | anthropic | groq |
                         together | openrouter
      --base-url URL     override the provider endpoint
      --key KEY          API key for this run
  -c, --continue         resume the most recent session in this directory
  -r, --resume [ID]      resume a session by id (no id: pick from a list)
      --plan             read-only: the agent may look but not change anything
      --yolo             skip every permission prompt
  -C, --cwd DIR          run against a different directory
      --shell NAME       powershell | cmd | bash | sh | zsh | auto
      --setup            configure provider, key, and model
      --no-color         plain output
  -v, --version
  -h, --help

inside a session, type /help for slash commands.
"""

HELP = """\
  /help              this list
  /model [name]      show or switch the model
  /models            list models the provider offers
  /clear             wipe the conversation and start fresh
  /compact           summarise the conversation to free context
  /cost              tokens and requests used this session
  /tools             list available tools
  /undo [#n]         revert the file changes from the last turn (or turn n)
  /changes           list the turns that changed files
  /review [what]     send an adversarial reviewer over the changes
  /mcp               list connected MCP servers and their tools
  /shell [name]      choose the shell commands run in
  /plan              read-only mode: look but do not touch
  /auto              stop asking for permission this session
  /ask               go back to asking for permission
  /init              write an AJNA.md describing this project
  /sessions          list recent sessions
  /resume <id>       load a previous session
  /cwd [dir]         show or change the working directory
  /config            show the resolved configuration
  /exit              leave (Ctrl-D also works)
"""

FLAGS_WITH_VALUE = {
    "-m": "model", "--model": "model",
    "--provider": "provider", "--base-url": "base_url",
    "--key": "api_key", "-C": "cwd", "--cwd": "cwd",
    "--shell": "shell",
}


def choose_shell(cfg, force=False):
    """Ask once which shell to run commands in, then remember the answer.

    Whatever is chosen is the only shell used — pick cmd and every command
    goes through cmd.exe, and the model is told to write cmd syntax.
    """
    from .tools import shell as shell_mod

    installed = shell_mod.available()
    if not installed:
        return cfg.get("shell") or "auto"
    if cfg.get("shell") and not force:
        return cfg["shell"]
    if not sys.stdin.isatty():
        return cfg.get("shell") or "auto"

    ui.out("")
    ui.out("  " + ui.c("Which shell should ajna run commands in?", "bold"))
    ui.out("  " + ui.c("Only the one you pick is used.", "grey"))
    ui.out("")
    options = list(installed) + ["auto"]
    for i, kind in enumerate(options, 1):
        note = ("follow this machine's default (%s)" % shell_mod.default_kind()
                if kind == "auto" else shell_mod.SHELL_SPECS[kind][2].split(".")[0])
        marker = ui.c(" ← current", "bcyan") if kind == cfg.get("shell") else ""
        ui.out("  %s %-12s %s%s" % (ui.c("%d)" % i, "bcyan"), kind,
                                    ui.c(note, "grey"), marker))
    ui.out("")

    default = options[0]
    try:
        answer = input(ui.c("  choose [1]: ", "byellow")).strip()
    except (EOFError, KeyboardInterrupt):
        ui.out("")
        answer = ""

    chosen = default
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        chosen = options[int(answer) - 1]
    elif answer.lower() in options:
        chosen = answer.lower()

    cfg["shell"] = chosen
    try:
        cfg.save()
    except OSError:
        ui.warn("could not save the shell choice")
    ui.info("shell → %s" % shell_mod.describe(chosen).split(" — ")[0])
    return chosen


def parse_args(argv):
    """Hand-rolled parsing; argparse costs more startup time than it earns."""
    opts = {
        "print": False, "continue": False, "resume": None, "mode": "ask",
        "setup": False, "cwd": None, "color": None, "prompt": [],
        "overrides": {},
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            raise SystemExit(0)
        if arg in ("-v", "--version"):
            sys.stdout.write("ajna %s\n" % __version__)
            raise SystemExit(0)
        if arg in ("-p", "--print"):
            opts["print"] = True
        elif arg in ("-c", "--continue"):
            opts["continue"] = True
        elif arg in ("-r", "--resume"):
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 1
                opts["resume"] = argv[i]
            else:
                opts["resume"] = "?"
        elif arg == "--plan":
            opts["mode"] = "plan"
        elif arg == "--yolo":
            opts["mode"] = "auto"
        elif arg == "--setup":
            opts["setup"] = True
        elif arg == "--no-color":
            opts["color"] = "never"
        elif arg in FLAGS_WITH_VALUE:
            if i + 1 >= len(argv):
                ui.err("%s needs a value" % arg)
                raise SystemExit(2)
            i += 1
            key = FLAGS_WITH_VALUE[arg]
            if key == "cwd":
                opts["cwd"] = argv[i]
            else:
                opts["overrides"][key] = argv[i]
        elif arg.startswith("--") and "=" in arg:
            key, _, value = arg[2:].partition("=")
            opts["overrides"][key.replace("-", "_")] = value
        elif arg.startswith("-") and len(arg) > 1:
            ui.err("unknown option %s (try --help)" % arg)
            raise SystemExit(2)
        else:
            opts["prompt"].append(arg)
        i += 1
    return opts


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = parse_args(argv)

    if opts["cwd"]:
        try:
            os.chdir(opts["cwd"])
        except OSError as exc:
            ui.err(exc)
            return 2

    cfg = config.load(opts["overrides"])
    ui.init(opts["color"] or cfg.color)

    if opts["setup"]:
        return setup(cfg)

    if not cfg.api_key and cfg.provider != "ollama-local":
        ui.warn("no API key configured for provider %r" % cfg.provider)
        if sys.stdin.isatty():
            return setup(cfg)
        ui.err("set AJNA_API_KEY or run: ajna --setup")
        return 2

    # Asked once, on the first interactive run; -p and pipes never block on it.
    if not opts["print"] and sys.stdin.isatty():
        choose_shell(cfg)

    root = os.getcwd()
    sid = session.new_id(root)
    permissions = perm.Permissions(cfg, opts["mode"])
    marks = None
    if cfg.get("checkpoints", True):
        from .checkpoint import prune
        prune()
        marks = Checkpoint(sid, root)
    ctx = Context(root, cfg, permit=permissions, checkpoint=marks)
    agent = Agent(cfg, ctx)

    if cfg.get("mcp_servers"):
        from . import mcp
        mcp.connect_all(cfg, quiet=opts["print"])

    if opts["continue"]:
        sid = _restore(agent, session.latest(root)) or sid
    elif opts["resume"]:
        target = opts["resume"]
        if target == "?":
            target = _pick_session(root)
        if target:
            sid = _restore(agent, target) or sid

    prompt_text = " ".join(opts["prompt"]).strip()
    try:
        if opts["print"] or not sys.stdin.isatty():
            if not prompt_text and not sys.stdin.isatty():
                prompt_text = sys.stdin.read().strip()
            if not prompt_text:
                ui.err("nothing to do: pass a prompt or pipe one in")
                return 2
            return one_shot(agent, sid, root, prompt_text)

        return repl(agent, sid, root, prompt_text, permissions, marks)
    finally:
        if cfg.get("mcp_servers"):
            from . import mcp
            mcp.shutdown()


# --- run modes --------------------------------------------------------------

def one_shot(agent, sid, root, prompt_text):
    try:
        text = agent.run(prompt_text)
    except ProviderError as exc:
        ui.err(exc)
        return 1
    except KeyboardInterrupt:
        return 130
    session.save(sid, root, agent.messages, {"model": agent.cfg.model})
    if not text.strip():
        return 1
    return 0


def repl(agent, sid, root, first_prompt, permissions, marks=None):
    _banner(agent, root)
    _install_readline()
    pending = first_prompt
    interrupts = 0

    while True:
        if pending:
            line, pending = pending, ""
        else:
            try:
                line = input(ui.c("› ", "bcyan")).strip()
                interrupts = 0
            except KeyboardInterrupt:
                interrupts += 1
                ui.out("")
                if interrupts >= 2:
                    ui.info("bye")
                    return 0
                ui.info("press Ctrl-C again to exit, or /exit")
                continue
            except EOFError:
                ui.out("")
                return 0

        if not line:
            continue

        if line.startswith("/"):
            action = slash(agent, line, root, permissions, marks)
            if action == "exit":
                _persist(agent, sid, root)
                return 0
            continue

        if marks:
            marks.begin_turn(line[:60])
        try:
            agent.run(line)
        except ProviderError as exc:
            ui.err(exc)
        except KeyboardInterrupt:
            agent.cancel.set()
            ui.out("")
            ui.info("interrupted")
        _persist(agent, sid, root)
        _status(agent)


def _persist(agent, sid, root):
    session.save(sid, root, agent.messages,
                 {"model": agent.cfg.model, "usage": agent.client.usage})


# --- slash commands ---------------------------------------------------------

def slash(agent, line, root, permissions, marks=None):
    parts = line[1:].split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    cfg = agent.cfg

    if cmd in ("exit", "quit", "q"):
        return "exit"

    if cmd == "help":
        ui.out(HELP)

    elif cmd == "model":
        if arg:
            cfg.model = arg
            agent.system = _rebuild_prompt(agent, root)
            ui.info("model → " + arg)
        else:
            ui.out("  model: %s\n  small: %s\n  provider: %s"
                   % (cfg.model, cfg.small_model, cfg.provider))

    elif cmd == "models":
        spinner = ui.Spinner("listing models").start()
        names = agent.client.models()
        spinner.stop()
        if not names:
            ui.warn("provider did not return a model list")
        else:
            for name in names:
                mark = ui.c(" ←", "bcyan") if name == cfg.model else ""
                ui.out("  " + name + mark)

    elif cmd == "clear":
        agent.reset()
        ui.info("conversation cleared")

    elif cmd == "compact":
        if not agent.compact():
            ui.info("nothing to compact yet")

    elif cmd == "cost":
        usage = agent.client.usage
        ui.out("  requests: %d\n  input tokens: %s\n  output tokens: %s\n"
               "  context now: ~%s tokens (%d messages)"
               % (usage["requests"], "{:,}".format(usage["in"]),
                  "{:,}".format(usage["out"]),
                  "{:,}".format(agent.tokens()), len(agent.messages)))

    elif cmd == "tools":
        from . import tools as tools_mod
        tools_mod.load()
        for name in tools_mod.ORDER:
            tool = tools_mod.REGISTRY[name]
            head = tool.description.strip().splitlines()[0]
            flag = ui.c(" ✎", "byellow") if tool.mutating else ""
            ui.out("  %-8s%s %s" % (ui.c(name, "bcyan"), flag, ui.c(head, "grey")))

    elif cmd == "plan":
        permissions.mode = "plan"
        ui.info("plan mode: reads and searches only, no changes")

    elif cmd == "auto":
        permissions.mode = "auto"
        ui.warn("auto mode: no permission prompts for this session")

    elif cmd == "ask":
        permissions.mode = "ask"
        ui.info("permission prompts back on")

    elif cmd == "shell":
        from .tools import shell as shell_mod
        if arg:
            if arg.lower() not in shell_mod.CHOICES:
                ui.warn("unknown shell %r — choose from %s"
                        % (arg, ", ".join(shell_mod.CHOICES)))
                return None
            cfg["shell"] = arg.lower()
            cfg.save()
            ui.info("shell → " + arg.lower())
        else:
            choose_shell(cfg, force=True)
        agent.system = _rebuild_prompt(agent, root)

    elif cmd == "review":
        _review(agent, arg, marks)

    elif cmd == "undo":
        _undo(marks, arg)

    elif cmd == "changes":
        if not marks or not marks.turns():
            ui.info("no file changes recorded this session")
        else:
            for turn in marks.turns():
                item = marks.summary(turn)
                touched = item["changed"] + ["%s (new)" % p for p in item["created"]]
                ui.out("  %s  %s" % (ui.c("#%d" % turn, "bcyan"),
                                     ui.c(item["label"][:50], "grey")))
                ui.out("      " + ", ".join(touched[:8]))

    elif cmd == "mcp":
        from . import mcp
        if not mcp.SERVERS:
            ui.info("no MCP servers connected — configure mcp_servers in %s"
                    % config.CONFIG_PATH)
        for name, server in mcp.SERVERS.items():
            ui.out("  %s  %s" % (ui.c(name, "bcyan"),
                                 ui.c("%d tools" % len(server.tools), "grey")))
            for tool in server.tools:
                ui.out("      %s_%s" % (name, tool.get("name", "?")))

    elif cmd == "init":
        from .prompt import INIT_PROMPT
        agent.run(INIT_PROMPT)

    elif cmd == "sessions":
        items = session.listing(root)
        if not items:
            ui.info("no saved sessions for this directory")
        for item in items:
            ui.out("  %s  %s  %s" % (
                ui.c(item["id"][:15], "bcyan"),
                ui.c("%d turns" % item["turns"], "grey"),
                item["title"]))

    elif cmd == "resume":
        target = arg or _pick_session(root)
        if target and _restore(agent, target):
            ui.info("resumed %s" % target)

    elif cmd == "cwd":
        if arg:
            try:
                os.chdir(os.path.expanduser(arg))
            except OSError as exc:
                ui.err(exc)
                return None
            agent.ctx.root = os.getcwd()
            agent.system = _rebuild_prompt(agent, agent.ctx.root)
            ui.info("cwd → " + agent.ctx.root)
        else:
            ui.out("  " + os.getcwd())

    elif cmd == "config":
        for key in sorted(config.DEFAULTS):
            value = cfg.get(key)
            if key == "api_key":
                value = (value[:6] + "…" + value[-4:]) if value else "(unset)"
            ui.out("  %-16s %s" % (key, value))
        ui.out(ui.c("  config file      %s" % config.CONFIG_PATH, "grey"))

    else:
        ui.warn("unknown command /%s — try /help" % cmd)
    return None


def _review(agent, arg, marks):
    """Send an adversarial reviewer over the work, on demand.

    Deliberately not automatic. A check the harness runs for you has to be
    cheap and certain; this one costs a model call and can be wrong, so it
    stays where the user can decide it is worth it.
    """
    from .tools.agents import ROLES

    target = arg.strip()
    if not target:
        touched = []
        if marks:
            for turn in marks.turns():
                item = marks.summary(turn)
                touched += item["changed"] + item["created"]
        touched = list(dict.fromkeys(touched))
        if not touched:
            ui.info("nothing changed this session — say /review <what> to aim it")
            return
        target = "the changes made to: " + ", ".join(touched[:20])

    if agent.ctx.spawn is None:
        ui.warn("sub-agents are unavailable here")
        return

    task = (
        "Review %s in this project.\n\n"
        "Look for what is actually wrong, not what could be prettier. In "
        "particular:\n"
        "- inputs the code accepts that it should reject, and the reverse\n"
        "- error paths that crash instead of failing cleanly\n"
        "- encoding, newline and platform assumptions\n"
        "- cases the tests do not cover that a user would hit\n\n"
        "Read the code, run the tests if there are any, and report findings "
        "most serious first, each with a path, a line, and how it fails. "
        "If you find nothing real, say so." % target)

    spinner = ui.Spinner("reviewing").start()
    try:
        report, steps = agent.ctx.spawn(
            task, ROLES["review"]["tools"], ROLES["review"]["system"], "review")
    except Exception as exc:
        spinner.stop()
        ui.err("review failed: %s" % exc)
        return
    spinner.stop()

    ui.out("")
    for line in (report or "(the reviewer returned nothing)").splitlines():
        ui.out(ui.render_line(line))
    ui.out("")
    ui.info("reviewed in %d steps — findings are suggestions, not facts" % steps)


def _undo(marks, arg):
    if not marks:
        ui.warn("checkpoints are disabled, nothing to undo")
        return
    turns = marks.turns()
    if not turns:
        ui.info("no file changes to undo")
        return

    target = turns[0]
    if arg:
        stripped = arg.lstrip("#")
        if not stripped.isdigit() or int(stripped) not in turns:
            ui.warn("no recorded turn %s — try /changes" % arg)
            return
        target = int(stripped)

    item = marks.summary(target)
    touched = item["changed"] + ["%s (delete)" % p for p in item["created"]]
    ui.out("  " + ui.c("about to revert turn #%d: %s" % (target, item["label"][:40]),
                       "byellow"))
    ui.out("  " + ui.c(", ".join(touched[:10]), "grey"))
    if not ui.confirm("revert these files?", default=True):
        ui.info("kept")
        return

    restored, deleted, failed = marks.undo(target)
    for path in restored:
        ui.out("  " + ui.c("reverted " + path, "bgreen"))
    for path in deleted:
        ui.out("  " + ui.c("removed " + path, "bgreen"))
    for path in failed:
        ui.out("  " + ui.c("could not revert " + path, "bred"))
    ui.info("the model is not aware of this revert — tell it what you undid")


def _rebuild_prompt(agent, root):
    from . import prompt as prompt_mod
    return prompt_mod.build(root, agent.cfg)


# --- helpers ----------------------------------------------------------------

def _banner(agent, root):
    ready = (time.time() - _T0) * 1000
    ui.out("")
    ui.out("  %s  %s" % (ui.c("ajna", "bold", "bcyan"),
                         ui.c("v" + __version__, "grey")))
    ui.out("  %s" % ui.c(agent.cfg.model + "  ·  " + _short(root), "grey"))
    if os.environ.get("AJNA_DEBUG"):
        ui.out("  %s" % ui.c("ready in %.0f ms" % ready, "grey"))
    ui.out("  %s" % ui.c("/help for commands · Ctrl-C to interrupt", "grey"))
    ui.out("")


def _short(path, limit=48):
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    return path if len(path) <= limit else "…" + path[-limit:]


def _status(agent):
    used = agent.tokens()
    budget = agent.cfg.context_tokens
    if used > budget * 0.6:
        ui.out(ui.c("  context %d%% full — /compact to reclaim"
                    % int(100 * used / budget), "grey"))


def _restore(agent, session_id):
    if not session_id:
        return None
    data = session.load(session_id)
    if not data:
        ui.warn("no session %r" % session_id)
        return None
    agent.messages = data.get("messages") or []
    ui.info("restored %d messages from %s" % (len(agent.messages), session_id[:15]))
    return session_id


def _pick_session(root):
    items = session.listing(root, limit=10)
    if not items:
        ui.info("no saved sessions here")
        return None
    ui.out("")
    for i, item in enumerate(items, 1):
        ui.out("  %s  %s  %s" % (ui.c("%d)" % i, "bcyan"),
                                 ui.c("%d turns" % item["turns"], "grey"),
                                 item["title"]))
    try:
        choice = input(ui.c("  pick a number (enter to skip): ", "byellow")).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(items):
        return None
    return items[int(choice) - 1]["id"]


def _install_readline():
    """Line editing and history, where the platform provides it."""
    try:
        import readline
    except ImportError:
        return
    history = os.path.join(config.HOME_DIR, "history")
    try:
        os.makedirs(config.HOME_DIR, exist_ok=True)
        readline.read_history_file(history)
    except OSError:
        pass
    readline.set_history_length(1000)
    import atexit
    atexit.register(lambda: _save_history(readline, history))


def _save_history(readline, path):
    try:
        readline.write_history_file(path)
    except OSError:
        pass


# --- setup ------------------------------------------------------------------

def setup(cfg):
    ui.out("")
    ui.out("  " + ui.c("ajna setup", "bold", "bcyan"))
    ui.out("")
    names = sorted(config.PROVIDERS)
    ui.out("  providers: " + ", ".join(names))
    try:
        provider = input(ui.c("  provider [%s]: " % cfg.provider, "byellow")).strip()
        if provider and provider not in config.PROVIDERS:
            ui.err("unknown provider %r" % provider)
            return 2
        cfg.provider = provider or cfg.provider
        preset = config.PROVIDERS[cfg.provider]

        current = cfg.api_key
        shown = (current[:6] + "…") if current else "unset"
        key = input(ui.c("  api key [%s]: " % shown, "byellow")).strip()
        if key:
            cfg.api_key = key

        model = input(ui.c("  model [%s]: " % (cfg.model or preset["default_model"]),
                           "byellow")).strip()
        cfg.model = model or cfg.model or preset["default_model"]
        base = input(ui.c("  base url [%s]: " % preset["base_url"],
                          "byellow")).strip()
        cfg.base_url = base or preset["base_url"]
    except (EOFError, KeyboardInterrupt):
        ui.out("")
        return 1

    ui.out("")
    spinner = ui.Spinner("verifying").start()
    client = Client(cfg)
    try:
        reply = client.complete(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=16)
        spinner.stop()
        ui.out("  " + ui.c("✓ %s responded" % cfg.model, "bgreen")
               + ui.c("  (%s)" % reply.strip()[:40], "grey"))
    except ProviderError as exc:
        spinner.stop()
        ui.err(exc)
        ui.warn("saving anyway — fix the key or model and rerun --setup")

    cfg.save()
    ui.out("  " + ui.c("saved to %s" % config.CONFIG_PATH, "grey"))
    ui.out("")
    ui.out("  start with: " + ui.c("ajna", "bcyan"))
    ui.out("")
    return 0
