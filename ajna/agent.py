"""The agent loop: stream a turn, run the tools it asks for, repeat."""

import json
import threading
import time

from . import prompt as prompt_mod
from . import repair
from . import tools as tools_mod
from . import ui
from .provider import Client, ProviderError

MAX_DEPTH = 2   # a sub-agent may not spawn sub-agents of its own


def estimate_tokens(messages):
    """Rough token count. Cheap and good enough for compaction decisions."""
    total = 0
    for msg in messages:
        total += len(msg.get("content") or "") // 4 + 4
        for call in msg.get("tool_calls") or []:
            total += len(json.dumps(call.get("args") or {})) // 4 + 8
    return total


class Agent:
    def __init__(self, cfg, ctx, client=None, on_event=None, system=None,
                 tool_filter=None, prefix="", show_text=True, model=None,
                 depth=0, max_steps=None):
        self.cfg = cfg
        self.ctx = ctx
        self.client = client or Client(cfg)
        self.messages = []
        self.on_event = on_event or (lambda *a: None)
        self.cancel = threading.Event()
        self.steps = 0
        self.system = system or prompt_mod.build(ctx.root, cfg)
        self.compactions = 0
        self.model = model or cfg.model
        self.depth = depth
        self.prefix = prefix              # indent for nested sub-agent output
        self.show_text = show_text        # sub-agents report only their summary
        self.tool_filter = tool_filter    # None means every registered tool
        self.max_steps = max_steps or cfg.max_steps
        self.turn = 0
        self._touched = []
        self._checked = True
        self._nudged = 0

        if depth == 0:
            # Sub-agents reach the parent through the shared context.
            ctx.spawn = self.spawn

    # -- history --------------------------------------------------------

    def _wire_messages(self):
        return [{"role": "system", "content": self.system}] + self.messages

    def _schemas(self):
        every = tools_mod.schemas()
        if self.tool_filter is None:
            return [s for s in every
                    if not (s["name"] == "agent" and self.depth >= MAX_DEPTH - 1)]
        return [s for s in every if s["name"] in self.tool_filter]

    # -- sub-agents -----------------------------------------------------

    def spawn(self, task, tool_names, system, label, model=None, max_steps=25):
        """Run a child agent on `task` and return its closing report."""
        child_ctx = self.ctx.child()
        child = Agent(
            self.cfg, child_ctx, client=self.client,
            system=system, tool_filter=set(tool_names),
            prefix=self.prefix + ui.c("│ ", "grey"),
            show_text=False, model=model or self.model,
            depth=self.depth + 1, max_steps=max_steps,
        )
        child.cancel = self.cancel
        report = child.run(task)
        # Anything the child read, the parent may now safely edit.
        self.ctx.read_files.update(child_ctx.read_files)
        return report, child.steps

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})

    def reset(self):
        self.messages = []
        self.ctx.read_files.clear()
        self.ctx.todos = []

    def tokens(self):
        return estimate_tokens(self._wire_messages())

    # -- main loop ------------------------------------------------------

    def run(self, user_input=None):
        """Run one exchange to completion. Returns the final assistant text."""
        if user_input:
            self.add_user(user_input)

        self.cancel.clear()
        self.steps = 0
        self.turn += 1
        final_text = ""
        schemas = self._schemas()
        self._touched = []        # files changed this turn, in order
        self._checked = True      # a check has happened since the last change
        self._nudged = 0          # how many times the check has spoken
        self.ctx.removed_names = set()

        while self.steps < self.max_steps:
            self.steps += 1
            self._maybe_compact()

            text, calls = self._one_turn(schemas)
            if self.cancel.is_set():
                self.messages.append({
                    "role": "user",
                    "content": "[The user interrupted. Stop and wait for direction.]",
                })
                return final_text

            entry = {"role": "assistant", "content": text}
            if calls:
                entry["tool_calls"] = [
                    {"id": c.id, "name": c.name, "args": c.args} for c in calls
                ]
            self.messages.append(entry)

            if text.strip():
                final_text = text

            if not calls:
                nudge = self._completeness_nudge()
                if nudge:
                    self.messages.append({"role": "user", "content": nudge})
                    continue
                return final_text

            results = self._run_tools(calls)
            for call, result in zip(calls, results):
                self.messages.append({
                    "role": "tool", "id": call.id, "name": call.name,
                    "content": result.content,
                })
            if self.cancel.is_set():
                return final_text

        ui.warn("stopped after %d steps — the task may be incomplete" % self.steps)
        return final_text

    def _one_turn(self, schemas):
        """Stream a single assistant message. Returns (text, tool_calls)."""
        renderer = ui.StreamRenderer()
        spinner = ui.Spinner("thinking")
        spinner.start()

        text_parts, calls = [], []
        thinking_shown = False
        started = False

        try:
            for kind, payload in self.client.stream(
                self._wire_messages(), tools=schemas, cancel=self.cancel,
                model=self.model,
            ):
                if kind == "thinking":
                    if not thinking_shown:
                        spinner.update("reasoning")
                        thinking_shown = True
                elif kind == "text":
                    text_parts.append(payload)
                    if not self.show_text:
                        continue
                    if not started:
                        spinner.stop()
                        started = True
                    renderer.feed(payload)
                elif kind == "tool_calls":
                    calls.extend(payload)
        except ProviderError as exc:
            spinner.stop()
            renderer.close()
            ui.err(exc)
            return "", []
        except KeyboardInterrupt:
            self.cancel.set()
            spinner.stop()
            renderer.close()
            ui.out("")
            ui.info("interrupted")
            return "".join(text_parts), []
        finally:
            spinner.stop()

        renderer.close()
        if started:
            ui.out("")
        return "".join(text_parts), calls

    # -- tools ----------------------------------------------------------

    def _run_tools(self, calls):
        """Execute tool calls, fanning out the ones that are safe in parallel."""
        for call in calls:
            self._repair(call)

        results = [None] * len(calls)
        batch = []   # indices of adjacent concurrent-safe calls

        def flush():
            if not batch:
                return
            if len(batch) == 1:
                idx = batch[0]
                results[idx] = self._invoke(calls[idx])
            else:
                self._invoke_parallel(calls, batch, results)
            batch.clear()

        for i, call in enumerate(calls):
            tool = tools_mod.get(call.name)
            if tool is not None and tool.concurrent:
                batch.append(i)
                continue
            flush()
            results[i] = self._invoke(call)
            if self.cancel.is_set():
                break
        flush()

        for i, result in enumerate(results):
            if result is None:
                results[i] = tools_mod.Result.fail("not executed (interrupted)")
        return results

    def _invoke_parallel(self, calls, indices, results):
        from concurrent.futures import ThreadPoolExecutor

        for i in indices:
            self._announce(calls[i])
        spinner = ui.Spinner("running %d tools" % len(indices))
        spinner.start()
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(indices))) as pool:
                futures = {pool.submit(self._call_tool, calls[i]): i for i in indices}
                for future, i in futures.items():
                    results[i] = future.result()
        finally:
            spinner.stop()
        for i in indices:
            self._report(calls[i], results[i], announced=True)

    def _invoke(self, call):
        self._announce(call)
        spinner = ui.Spinner(call.name)
        tool = tools_mod.get(call.name)
        # A permission prompt would fight with the spinner for the line.
        quiet = tool is not None and tool.mutating
        if not quiet:
            spinner.start()
        try:
            result = self._call_tool(call)
        finally:
            spinner.stop()
        self._report(call, result, announced=True)
        return result

    def _completeness_nudge(self):
        """Facts, not homework, when the model stops right after editing.

        The first version of this asked the model to go and verify its work.
        It lifted the larger model from 80% to 95% on the benchmark and pushed
        the small one from 90% down to 65%: told to check, a weak model reads
        that as "keep editing", and it broke files that were already correct.

        So the harness does the checking itself and speaks only when it has
        something concrete — a file that no longer parses, or a name that was
        renamed in one place and left behind in another. When the checks come
        back clean, the turn simply ends.
        """
        if self._nudged >= 2 or not self._touched:
            return None

        problems = self._objective_problems()
        if problems:
            self._nudged += 1
            return (
                "[Automatic check of the files you just changed found real "
                "problems:\n\n%s\n\nFix exactly these. Change nothing else.]"
                % "\n".join("- " + p for p in problems[:8]))

        if self._checked or self._nudged:
            return None

        if not self._has_tests():
            return None

        self._nudged += 1
        return (
            "[You changed %s and did not run anything afterwards. This project "
            "has tests: run them and report the real output. Do not edit any "
            "file unless a test actually fails.]"
            % ", ".join(dict.fromkeys(self._touched)))

    def _objective_problems(self):
        """Checks the harness can make itself, with no model judgement."""
        from .tools import files as files_mod

        problems = []
        for path in dict.fromkeys(self._touched):
            full = self.ctx.resolve(path)
            text, _style, error = files_mod._read_text(full)
            if error or text is None:
                continue
            bad = repair.validate_syntax(full, text)
            if bad:
                problems.append("%s no longer parses: %s" % (self.ctx.rel(full), bad))

        names = sorted(self.ctx.removed_names)
        if names:
            for hit in files_mod._dangling_references(self.ctx, "", names):
                problems.append(
                    "%s — that name was removed, so this call site is now broken"
                    % hit)
        return problems

    def _has_tests(self):
        """Whether running a test suite is even a meaningful suggestion."""
        import os

        try:
            for name in os.listdir(self.ctx.root):
                low = name.lower()
                if low.startswith("test") or low.endswith("_test.py") or low == "tests":
                    return True
        except OSError:
            pass
        return False

    def _note_tool_effect(self, call, result):
        """Track whether the turn has changed files without checking them."""
        if result.error:
            return
        tool = tools_mod.get(call.name)
        if tool is not None and tool.mutating and call.name in ("write", "edit"):
            path = call.args.get("path") if isinstance(call.args, dict) else None
            self._touched.append(str(path or call.name))
            self._checked = False
        elif call.name in ("shell", "grep", "agent"):
            # Only these can show that a change is complete; re-reading the
            # file you just wrote proves nothing about the rest of the project.
            self._checked = True

    def _repair(self, call):
        """Correct near-miss tool names and argument names before dispatch.

        Done up front so the line the user sees names the tool that actually
        ran, and so a small model's habits cost nothing.
        """
        known = set(self._visible_tools())
        name, note = repair.fix_name(call.name, known)
        if note:
            call.name = name
            self._note = note
        tool = tools_mod.get(call.name)
        if tool is not None:
            call.args, _ = repair.fix_args(tool, call.args)

    def _visible_tools(self):
        tools_mod.load()
        if self.tool_filter is None:
            return set(tools_mod.REGISTRY)
        return set(self.tool_filter) & set(tools_mod.REGISTRY)

    def _call_tool(self, call):
        visible = self._visible_tools()
        tool = tools_mod.get(call.name) if call.name in visible else None
        if tool is None:
            return tools_mod.Result.fail(
                "unknown tool %r. Available tools: %s"
                % (call.name, ", ".join(sorted(visible))))

        args = call.args if isinstance(call.args, dict) else {}
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            spec = tool.parameters.get("properties", {})
            hint = "; ".join(
                "%s (%s): %s" % (k, spec.get(k, {}).get("type", "string"),
                                 spec.get(k, {}).get("description", ""))
                for k in missing)
            return tools_mod.Result.fail(
                "missing required argument(s) for %s: %s" % (call.name, hint))
        kwargs = dict(args)

        started = time.time()
        try:
            result = tool.fn(self.ctx, **kwargs)
        except TypeError as exc:
            return tools_mod.Result.fail("bad arguments for %s: %s" % (call.name, exc))
        except KeyboardInterrupt:
            self.cancel.set()
            return tools_mod.Result.fail("interrupted by user")
        except Exception as exc:
            return tools_mod.Result.fail(
                "%s raised %s: %s" % (call.name, type(exc).__name__, exc))

        if not isinstance(result, tools_mod.Result):
            result = tools_mod.Result(str(result))
        result.meta["duration"] = time.time() - started
        return result

    def _announce(self, call):
        tool = tools_mod.get(call.name)
        label = tool.label(call.args) if tool else call.name
        ui.out("%s%s %s" % (self.prefix, ui.c("●", "bcyan"), label))
        self.on_event("tool_start", call)

    def _report(self, call, result, announced=False):
        self._note_tool_effect(call, result)
        if result.error:
            first = result.content.splitlines()[0] if result.content else "failed"
            ui.out(self.prefix + ui.c("  " + first[:300], "bred"))
        elif result.display:
            for line in result.display.split("\n"):
                ui.out(self.prefix + line)
        self.on_event("tool_end", (call, result))

    # -- context management ---------------------------------------------

    def _maybe_compact(self):
        budget = self.cfg.context_tokens * self.cfg.compact_at
        if self.tokens() < budget or len(self.messages) < 6:
            return
        self.compact()

    def compact(self):
        """Replace old history with a summary, keeping the recent tail intact."""
        if len(self.messages) < 4:
            return False

        keep = 4
        head, tail = self.messages[:-keep], self.messages[-keep:]
        # Never begin the retained tail with an orphaned tool result.
        while tail and tail[0]["role"] == "tool":
            head.append(tail.pop(0))
        if not head:
            return False

        ui.info("compacting context (%d tokens)…" % self.tokens())
        transcript = _flatten(head)
        try:
            summary = self.client.complete(
                [{"role": "user",
                  "content": transcript + "\n\n---\n\n" + prompt_mod.COMPACT_PROMPT}],
                model=self.cfg.small_model or self.cfg.model,
                max_tokens=1500,
            )
        except ProviderError as exc:
            ui.warn("compaction failed (%s), trimming instead" % exc)
            summary = ""

        if summary.strip():
            replacement = [{"role": "user",
                            "content": "[Summary of earlier work in this session]\n\n"
                                       + summary.strip()}]
        else:
            replacement = [{"role": "user",
                            "content": "[Earlier conversation was dropped to free context.]"}]

        self.messages = replacement + tail
        self.compactions += 1
        ui.info("context compacted to %d tokens" % self.tokens())
        return True


def _flatten(messages, limit=60000):
    """Render history as plain text for the summariser."""
    chunks = []
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            body = (msg.get("content") or "")[:1500]
            chunks.append("[tool %s]\n%s" % (msg.get("name", "?"), body))
        else:
            body = msg.get("content") or ""
            for call in msg.get("tool_calls") or []:
                body += "\n[called %s(%s)]" % (
                    call["name"], json.dumps(call.get("args") or {})[:300])
            chunks.append("[%s]\n%s" % (role, body.strip()))
    text = "\n\n".join(chunks)
    return text[-limit:]
