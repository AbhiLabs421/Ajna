# ajna

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)

A terminal coding agent in pure Python. No third-party packages, no build step,
no virtualenv. Cold start is about 180 ms.

It runs on free models too: a repair layer absorbs the mistakes small models
make, so `gpt-oss:20b` on Ollama's free tier completes tasks that otherwise
need a frontier model. Bring your own key for any provider, or run fully local.

## Demo

<!-- Record with: python scripts/demo.py   then drop the GIF in here:
     ![ajna fixing a failing test](docs/demo.gif) -->

ajna reads the code, finds the bug, and fixes it on its own. See it run on a
throwaway sample project — nothing in your own files is touched:

```bash
python scripts/demo.py
```

## Install

Clone the repository — there is nothing else to download, and no build step:

```bash
git clone https://github.com/AbhiLabs421/Ajna.git
cd Ajna
```

Install it as a console script (needs only Python 3.8+):

```bash
pip install -e .
```

That puts an `ajna` command on your `PATH`. Prefer not to install? Add the
cloned folder to your `PATH` and run the `ajna` launcher directly — on Windows
use `ajna.cmd`, on macOS or Linux symlink `ajna.sh` as `ajna` somewhere on your
`PATH`.

## Configure

```bash
ajna --setup
```

This asks for a provider, an API key, a model, and the base URL (it defaults to
the provider's own endpoint — just press Enter unless you point at a custom
one), verifies the model answers, and writes `~/.ajna/config.json`. The key is
stored in plain text there, so treat that file as a secret; `AJNA_API_KEY` in
the environment overrides it, as does `AJNA_BASE_URL` for the endpoint.

Providers: `ollama` (Ollama Cloud), `ollama-local`, `openai`, `anthropic`,
`groq`, `together`, `openrouter`. Anything OpenAI-compatible works by choosing
`openai` and giving its URL at the base-url prompt (or `--provider openai
--base-url <url>`).

## Use

```bash
ajna                                  # interactive session
ajna "why does the build fail?"       # interactive, with a first prompt
ajna -p "summarise src/parser.py"     # one-shot, prints and exits
git diff | ajna -p "review this"      # reads a piped prompt
```

Useful flags:

| flag | meaning |
| --- | --- |
| `-m, --model` | model for this run |
| `-c, --continue` | resume the last session in this directory |
| `-r, --resume [id]` | resume a specific session, or pick from a list |
| `--plan` | read-only: the agent may look but not change anything |
| `--yolo` | skip every permission prompt |
| `-C, --cwd DIR` | run against another directory |
| `--shell NAME` | `powershell`, `cmd`, `bash`, `sh`, `zsh`, or `auto` |

Inside a session, `/help` lists the slash commands: `/model`, `/models`,
`/clear`, `/compact`, `/cost`, `/tools`, `/undo`, `/changes`, `/review`,
`/mcp`,
`/shell`, `/plan`, `/auto`, `/ask`, `/init`, `/sessions`, `/resume`, `/cwd`,
`/config`, `/exit`.

## Choosing a shell

The first interactive run asks which shell commands should go through, and
remembers the answer:

```
  Which shell should ajna run commands in?
  Only the one you pick is used.

  1) powershell   PowerShell
  2) cmd          Windows cmd.exe
  3) bash         bash
  4) auto         follow this machine's default (powershell)
```

Pick `cmd` and every command runs through `cmd.exe` — no PowerShell, ever. The
choice also goes into the system prompt along with that shell's syntax rules,
so the model writes `dir` and `%VAR%` for cmd instead of PowerShell cmdlets.
Change it later with `/shell`, or per run with `--shell cmd`.

## Tools

| tool | what it does |
| --- | --- |
| `read` | line-numbered file read with offset/limit |
| `write` | full-file write; refuses to clobber a file it has not read |
| `edit` | exact-string replace, unique match enforced, shows a diff |
| `ls` | directory tree, skipping `.git`, `node_modules`, and friends |
| `glob` | find files by pattern, newest first |
| `grep` | regex search across files, fanned out over a thread pool |
| `shell` | run a command in the shell you chose (see below) |
| `todo` | track a multi-step plan |
| `fetch` | retrieve a URL as text |
| `agent` | delegate a job to a sub-agent with its own context |

Independent read-only tools in the same turn run in parallel.

## Sub-agents

`agent` runs a child agent with its own message history and a restricted
toolset. Its tool output never enters the main conversation — only its closing
report does. Reading nine files to answer one question costs the main thread a
paragraph instead of nine files, which matters most when the model's useful
context is short.

Three roles: `explore` (read-only research), `build` (scoped implementation),
`review` (read-only critique, may run tests). Several launched in one turn run
in parallel. Sub-agents cannot spawn sub-agents.

## Undo

Every file is snapshotted before it is changed. `/changes` lists the turns that
touched files; `/undo` reverts the last one, `/undo #2` a specific one. Undo
works per turn, not per edit, because that is the unit people think in. It
does not touch git, so it works outside a repository and never disturbs staged
work. Snapshots live in `~/.ajna/checkpoints/` and are pruned automatically.

The model is not told about a revert — say what you undid, or it will assume
its earlier changes are still in place.

## MCP servers

Configure Model Context Protocol servers in `~/.ajna/config.json`:

```json
{
  "mcp_servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "..."}
    }
  }
}
```

Their tools are discovered at startup and registered as `<server>_<tool>`;
`/mcp` lists them. Every remote call asks permission, since a server can do
anything. With no servers configured this costs nothing at startup.

## Making small models work

A frontier model calls the tools it was given. Smaller models drift, and the
harness absorbs it rather than failing the turn:

- **Tool-name routing.** `exec`, `bash`, `str_replace`, `read_file`, `search`
  and about sixty other invented names map onto the real tool.
- **Argument aliasing.** `file_path`, `old_string`, `target_file`, `query` and
  friends are renamed to what the schema expects; string `"50"` and `"true"`
  are coerced to the right types; unknown arguments are dropped instead of
  raising.
- **Diff-marker guard.** Replacement text whose lines start with `+`/`-` is
  rejected before it reaches the file — a real corruption seen in testing.
- **Syntax check after every write.** Python, JSON, XML and TOML are parsed
  after the change. A write that leaves a file unparseable comes back as an
  *error*, not a note: the same warning appended to a successful "Created
  logstat.py (97 lines)" was read as done, and one run shipped a file with an
  IndentationError.
- **Deletion notice.** If an edit removes a function or class that existed
  before, the result says which — small models replace when asked to add.
- **Regex fallback.** `grep "total("` is not valid regex; it is searched for
  literally rather than costing a turn.

Measured effect: the same failing-test task that took `gpt-oss:120b` 81
seconds and left a corrupted file before this layer existed now completes on
`gpt-oss:20b` — the smallest model available — in 15 seconds, correctly.

## Safety

- Writes, shell commands, and network fetches ask before running. Answer `a` to
  save a rule like `shell:git *` for next time; rules live in the config file.
- Read-only commands (`git status`, `ls`, …) never prompt.
- Destructive commands (recursive force-delete, disk formatting, force push,
  piping a download into a shell) are refused outright, even under `--yolo`.
- `--plan` denies every mutation, which is the safe way to let it explore an
  unfamiliar repository.
- Content from files, command output, and fetched pages is treated as data. The
  system prompt tells the agent to surface instructions found there rather than
  follow them.

## Project context

Drop an `AJNA.md` at the repository root and it is loaded into every session.
`/init` writes a first draft by exploring the codebase. `AGENTS.md`,
`CLAUDE.md`, and `.cursorrules` are picked up too, so an existing repository
usually needs no setup.

## Context management

Conversations are compacted automatically at 75% of the model's context
window: older turns are summarised by the small model and replaced, while the
recent tail is kept verbatim. `/compact` forces it early, `/cost` shows where
you stand.

Sessions are saved to `~/.ajna/sessions/` after every turn, so closing the
terminal does not lose the thread.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Benchmark

Claims about an agent are worth what their measurement is worth, so there is
one:

```bash
python bench/run.py -m gpt-oss:120b --repeat 2
```

Ten tasks, each run in a fresh temporary directory with `--yolo -p`, each
scored by running code rather than by reading what the model said: fix a bug
the tests catch, wire a feature end to end, rename across files, search and
cite, add tests without deleting the existing ones, edit JSON precisely,
remove duplication without changing behaviour, count files with the shell,
repair a file that does not parse, and answer a question without touching
anything.

That last one matters as much as the rest — an agent that edits when you only
asked a question is a liability.

## Layout

```
ajna/
  cli.py        argument parsing, REPL, slash commands
  agent.py      the tool loop, parallel dispatch, sub-agents, compaction
  provider.py   Ollama / OpenAI / Anthropic streaming over urllib
  repair.py     absorbs small-model mistakes before they cost a turn
  checkpoint.py per-turn file snapshots behind /undo
  mcp.py        MCP client, JSON-RPC over stdio
  tools/        read write edit ls glob grep shell todo fetch agent
  prompt.py     system prompt and project-context discovery
  perm.py       permission gate and rule matching
  session.py    session persistence
  config.py     layered configuration
  ui.py         colour, spinner, streaming markdown, diffs
```

## Measured results

The benchmark is what settles arguments about whether a change helped. Ten
tasks, two runs each, on the two models the free Ollama Cloud tier allows:

| build | gpt-oss:120b | gpt-oss:20b | samples each |
| --- | --- | --- | --- |
| v1 — no completeness check | 80% | 90% | 10 (noisy) |
| v2 — "go and verify your work" | **95%** | **65%** | 20 |
| v3 — harness checks, facts only | 90% | 80% | 20 |

v2 is the row worth staring at. Telling the model to go and verify lifted the
larger model and wrecked the smaller one: read as an instruction to keep
working, a weak model edits files that were already correct — across those
runs it dropped a key from a JSON file, produced JSON that no longer parsed,
and deleted a test it had been told to keep. Mean time per task doubled, from
16s to 33s.

So v3 inverts it. The harness runs the checks itself — does every changed file
still parse, is any removed name still referenced somewhere — and speaks only
when it has a specific fact to report. When the checks come back clean the
turn simply ends, with nothing said.

Read the numbers honestly. v2 → v3 on the small model, 65% → 80% at equal
sampling, is a real gain from the redesign. Against v1 it is not: v1's figures
come from a single run of each task, and 85% aggregate for v1 versus 85% for
v3 is well inside the noise those sample sizes carry. What v3 buys that shows
clearly is consistency — the spread between the two models narrows from 30
points to 10, which is what you want from scaffolding that is supposed to be
model-agnostic.

The remaining v3 failures are precision failures — three of the four are the
model not following an explicit instruction it was given and warned about, not
the harness losing information. That is a model ceiling, and no amount of
scaffolding moves it.

The general lesson: scaffolding that asks a model to do more is not free, and
only measurement tells you which way it cut.

## Comparing against a frontier model

`bench/human_run.py` lays out the same ten tasks for an agent that is not
ajna, and scores the result with the same verifiers:

```bash
python bench/human_run.py setup /tmp/run      # writes the tasks, prints the prompts
# ... another agent does the work ...
python bench/human_run.py verify /tmp/run     # same scoring as ajna gets
```

Claude Opus 5, driven through the Claude Code harness, scored 10/10 on it.

That number should be read with more suspicion than it will invite:

- **The scorer wrote the tasks.** Opus authored these verifiers and knew
  exactly what each one checks. That is contamination, and it is the single
  biggest reason to discount the result.
- **It is a different harness.** This is Opus inside Claude Code, not Opus
  inside ajna. The pair is what was measured, not the model. Opus driven
  through ajna would be a separate, unmeasured number.
- **It had already seen the failure modes.** These exact tasks had been run
  against gpt-oss twice in the same session, so the traps were known.
- **n = 1 per task**, the same small-sample weakness called out above.

So it is not a clean win over anything. What it does establish is narrower and
still useful: every one of these tasks is solvable, and the ajna failures that
remain are the model missing an instruction it was given, not the harness
losing information. A clean comparison needs Opus run *through ajna*, on tasks
nobody involved has seen:

```bash
python bench/run.py --provider anthropic -m claude-opus-4-5 --repeat 2
```

That has not been run — this machine has no Anthropic key.

## Building a whole project from a spec

`trial/` holds a harder test than the task benchmark: a spec, and three
independent implementations of it.

`trial/SPEC.md` describes `logstat`, a Common Log Format analyser with
filters, deterministic tie-breaking, a JSON contract and defined exit codes.
`trial/acceptance.py` checks 34 properties of any implementation purely
through its command line. Both were written before any code existed.

**Spec compliance.** Opus 5, ajna with gpt-oss:120b, and ajna with
gpt-oss:20b each scored 34/34 on the first attempt. On this axis the model
genuinely does not matter.

**Behaviour past the spec.** `trial/differential.py` feeds every
implementation the same malformed input and reports where they disagree. Six
cases diverged. Four were real defects in the generated code: an unanchored
regex that accepted corrupt lines, a negative byte count that reduced the
total, a crash printing non-ASCII under a redirect, and a hard failure on one
undecodable byte.

Two of those are Windows encoding traps — the same class of bug that ajna
itself shipped with until the benchmark caught it. That is knowledge the
harness has and the model does not, so it now goes in the system prompt as
exact code to copy. Stating it as a judgement instead ("decide what should
happen on bad bytes") made things *worse*: a small model applied half of it,
adding `encoding=` without `errors=`, converting a working case into a crash.
Concrete beat advisory, measurably.

The two remaining divergences — whether a quoted request that breaks out of
its quotes is malformed, and whether a negative size is — are judgement calls
the model has to make. No harness check decides those.

**Run-to-run reliability, which is the honest weak point.** Six runs of the
same prompt with gpt-oss:20b scored 34, 34, 32, 33, 27 and 0 out of 34. The
zero shipped a file with an IndentationError. ajna's syntax guard had already
detected it and said so — but inside an otherwise successful "Created
logstat.py (97 lines)", which reads as done. A write that leaves a file
unparseable is now an error result, not a footnote. Four runs since: 34, 32,
33, 33. Small sample, and the claim is correspondingly small: the catastrophic
mode is gone, the typical run is unchanged.

Scoring note: `acceptance.py` reports out of all 34 checks even when a run
dies early. It used to report only the checks it reached, which scored a run
that crashed immediately as 0/1 rather than 0/34.
