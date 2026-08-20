"""The `agent` tool: delegate a self-contained job to a child agent.

The point is context economy. Reading nine files to answer "where is auth
handled?" costs the main conversation nine files of context; delegating it
costs one paragraph. That matters most with a small model, whose useful
context is short and whose accuracy falls off as history grows.

A sub-agent gets its own message history and a restricted toolset. It cannot
spawn further sub-agents.
"""

from . import Result, register
from .. import ui

ROLES = {
    "explore": {
        "tools": ("read", "ls", "glob", "grep", "fetch"),
        "label": "explore",
        "system": """\
You are a research sub-agent. You are given one question about a codebase and
you answer it — you do not change anything, and you have no tools that could.

Search widely before you conclude. Use glob and grep to find candidates, read
the files that matter, and follow the references you find.

Your reply is the only thing that reaches the main agent; it never sees your
tool output. So the reply must stand alone:

- Answer the question directly in the first sentence.
- Cite specific paths and line numbers, as `src/auth/session.py:88`.
- Quote the few lines that actually matter, not whole files.
- Say what you looked for and did not find — that is a real result.
- Do not pad. No preamble, no offer to help further.
""",
    },
    "build": {
        "tools": ("read", "write", "edit", "ls", "glob", "grep", "shell", "todo"),
        "label": "build",
        "system": """\
You are an implementation sub-agent with one scoped task. Do that task and
nothing beyond it — the main agent is handling the rest.

Read before you edit. Match the conventions already in the file. If the
project has tests covering what you touched, run them.

Your reply is the only thing the main agent sees. Report:

- What you changed, file by file, with paths.
- What you ran to verify it, and the actual result.
- Anything you could not do, and why.

Never claim a test passed without having run it.
""",
    },
    "review": {
        "tools": ("read", "ls", "glob", "grep", "shell"),
        "label": "review",
        "system": """\
You are a review sub-agent. Examine the work described and look for what is
actually wrong with it: bugs, missed cases, broken assumptions, things the
change silently deleted.

You may run tests and linters. You may not edit files.

Report only findings you are confident about, most serious first, each with a
path, a line, and a one-line explanation of how it fails. If you find nothing
wrong, say so plainly — a short honest report beats an invented one.
""",
    },
}


@register(
    "agent",
    """
    Delegate a self-contained job to a sub-agent with its own context.

    Use it when a job needs a lot of reading to produce a small answer — "find
    where X is implemented", "check whether Y is used anywhere", "review the
    change I just made". The sub-agent's tool output never enters this
    conversation; only its final report does.

    Roles: "explore" (read-only research), "build" (scoped implementation),
    "review" (read-only critique, may run tests).

    Give it everything it needs in one shot: it cannot ask you questions, and
    it cannot see this conversation. Launch several in the same turn when the
    jobs are independent — they run in parallel.
    """,
    {
        "task": {
            "type": "string",
            "description": "The complete, self-contained job. State what to find "
                           "or do, and what the reply should contain.",
        },
        "role": {
            "type": "string",
            "enum": ["explore", "build", "review"],
            "description": "Which kind of sub-agent to run (default: explore).",
        },
        "label": {
            "type": "string",
            "description": "Three or four words naming the job, shown to the user.",
        },
    },
    required=["task"],
    concurrent=True,
    summary=lambda a: "agent[%s] %s" % (
        a.get("role", "explore"),
        a.get("label") or (a.get("task", "")[:50] + "…")),
)
def agent(ctx, task, role="explore", label=None):
    if ctx.spawn is None:
        return Result.fail(
            "sub-agents are not available here (already inside one). "
            "Do the work directly with your own tools.")

    role = role if role in ROLES else "explore"
    spec = ROLES[role]
    task = (task or "").strip()
    if len(task) < 15:
        return Result.fail(
            "the task is too vague to delegate. A sub-agent cannot see this "
            "conversation, so state the whole job in one self-contained brief.")

    try:
        report, steps = ctx.spawn(
            task, spec["tools"], spec["system"], label or spec["label"])
    except Exception as exc:
        return Result.fail("sub-agent failed: %s: %s" % (type(exc).__name__, exc))

    if not (report or "").strip():
        return Result.fail(
            "the sub-agent finished without reporting anything. Try a narrower "
            "task, or do it yourself.")

    return Result(
        "Report from the %s sub-agent:\n\n%s" % (role, report.strip()),
        display=ui.c("  %s returned after %d step%s"
                     % (role, steps, "" if steps == 1 else "s"), "grey"),
        meta={"steps": steps},
    )
