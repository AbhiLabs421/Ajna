"""Tests for sub-agents, undo, the repair layer, and the MCP client."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ajna import config, repair, tools                    # noqa: E402
from ajna.agent import Agent                              # noqa: E402
from ajna.checkpoint import Checkpoint                    # noqa: E402
from ajna.tools import files                              # noqa: E402


def make_ctx(root, checkpoint=None):
    return tools.Context(root, config.load(), permit=lambda *a, **k: True,
                         checkpoint=checkpoint)


class RepairNameTest(unittest.TestCase):
    def setUp(self):
        self.known = set(tools.load())

    def test_invented_names_route_to_real_tools(self):
        """The exact failure seen in the first live run: the model called `exec`."""
        for invented, expected in (
            ("exec", "shell"), ("bash", "shell"), ("run_command", "shell"),
            ("read_file", "read"), ("cat", "read"),
            ("str_replace", "edit"), ("apply_patch", "edit"),
            ("list_dir", "ls"), ("search", "grep"), ("find_files", "glob"),
            ("web_fetch", "fetch"), ("task", "agent"),
        ):
            name, note = repair.fix_name(invented, self.known)
            self.assertEqual(name, expected, "%s should route to %s" % (invented, expected))
            self.assertTrue(note)

    def test_namespaced_names_are_unwrapped(self):
        self.assertEqual(repair.fix_name("functions.read", self.known)[0], "read")
        self.assertEqual(repair.fix_name("tool-grep", self.known)[0], "grep")

    def test_real_names_pass_through_untouched(self):
        for name in self.known:
            fixed, note = repair.fix_name(name, self.known)
            self.assertEqual(fixed, name)
            self.assertIsNone(note)

    def test_genuinely_unknown_name_is_left_alone(self):
        name, note = repair.fix_name("teleport", self.known)
        self.assertEqual(name, "teleport")
        self.assertIsNone(note)


class RepairArgsTest(unittest.TestCase):
    def setUp(self):
        tools.load()

    def test_alias_arguments_are_renamed(self):
        fixed, note = repair.fix_args(tools.get("read"), {"file_path": "a.py"})
        self.assertEqual(fixed, {"path": "a.py"})
        self.assertIn("file_path", note)

    def test_edit_alias_arguments(self):
        fixed, _ = repair.fix_args(
            tools.get("edit"),
            {"target_file": "a.py", "old_string": "x", "new_string": "y"})
        self.assertEqual(fixed, {"path": "a.py", "old": "x", "new": "y"})

    def test_unknown_arguments_are_dropped_not_raised(self):
        fixed, _ = repair.fix_args(tools.get("read"),
                                   {"path": "a.py", "explanation": "because"})
        self.assertEqual(fixed, {"path": "a.py"})

    def test_string_numbers_are_coerced(self):
        fixed, _ = repair.fix_args(tools.get("read"), {"path": "a", "limit": "50"})
        self.assertEqual(fixed["limit"], 50)

    def test_string_booleans_are_coerced(self):
        fixed, _ = repair.fix_args(
            tools.get("edit"),
            {"path": "a", "old": "x", "new": "y", "replace_all": "true"})
        self.assertIs(fixed["replace_all"], True)

    def test_json_string_array_is_parsed(self):
        fixed, _ = repair.fix_args(
            tools.get("todo"),
            {"items": '[{"task": "a", "status": "pending"}]'})
        self.assertIsInstance(fixed["items"], list)


class EditGuardTest(unittest.TestCase):
    """The stray `+ ` that corrupted cli.py in the live demo."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ctx = make_ctx(self.dir)
        self.path = os.path.join(self.dir, "m.py")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        files.read(self.ctx, "m.py")

    def test_diff_markers_are_rejected(self):
        result = files.edit(self.ctx, "m.py", "def a():\n    return 1",
                            "+def a():\n+    return 99\n+    # more\n+    # lines")
        self.assertTrue(result.error)
        self.assertIn("diff", result.content)
        with open(self.path, encoding="utf-8") as fh:
            self.assertIn("return 1", fh.read())

    def test_markdown_fence_in_write_is_rejected(self):
        result = files.write(self.ctx, "new.py", "```python\nx = 1\n```")
        self.assertTrue(result.error)
        self.assertIn("fence", result.content)

    def test_ordinary_minus_signs_are_not_mistaken_for_a_diff(self):
        result = files.edit(self.ctx, "m.py", "    return 1", "    return -1")
        self.assertFalse(result.error, result.content)

    def test_breaking_the_syntax_is_reported_as_a_failure(self):
        """A note inside a success message gets ignored; a failure does not.

        Observed: a run shipped a file with an IndentationError even though
        the guard had detected and reported it, because the report arrived
        appended to "Created logstat.py (97 lines)".
        """
        result = files.edit(self.ctx, "m.py", "def a():", "def a(:")
        self.assertTrue(result.error)
        self.assertIn("unparseable", result.content)
        self.assertIn("syntax error", result.content)
        # The text is still written, so the model can read it back and repair.
        with open(self.path, encoding="utf-8") as fh:
            self.assertIn("def a(:", fh.read())

    def test_failing_to_finish_repairing_a_broken_file_is_not_a_failure(self):
        """Editing an already-broken file is normal progress, not a regression."""
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("def a(:\n    return 1\n\n\ndef b(:\n    return 2\n")
        files.read(self.ctx, "m.py")
        result = files.edit(self.ctx, "m.py", "def a(:", "def a():")
        self.assertFalse(result.error, result.content)
        self.assertIn("still has", result.content)

    def test_a_clean_edit_reports_nothing(self):
        result = files.edit(self.ctx, "m.py", "return 1", "return 11")
        self.assertFalse(result.error)
        self.assertNotIn("syntax", result.content)

    def test_removed_definitions_are_named(self):
        result = files.edit(self.ctx, "m.py", "def b():\n    return 2",
                            "def c():\n    return 3")
        self.assertIn("removed definition", result.content)
        self.assertIn("b", result.content)

    def test_pure_addition_gets_no_removal_note(self):
        result = files.edit(self.ctx, "m.py", "def b():",
                            "def extra():\n    return 0\n\n\ndef b():")
        self.assertNotIn("removed definition", result.content)


class ValidateSyntaxTest(unittest.TestCase):
    def test_python(self):
        self.assertIsNone(repair.validate_syntax("a.py", "x = 1\n"))
        self.assertIn("syntax error", repair.validate_syntax("a.py", "def f(:\n"))

    def test_json(self):
        self.assertIsNone(repair.validate_syntax("a.json", '{"a": 1}'))
        self.assertIsNotNone(repair.validate_syntax("a.json", '{"a": 1,}'))

    def test_unknown_extension_is_not_guessed_at(self):
        self.assertIsNone(repair.validate_syntax("a.rs", "fn main() { ["))


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.marks = Checkpoint("test-session-%d" % os.getpid(), self.dir)
        self.ctx = make_ctx(self.dir, checkpoint=self.marks)

    def tearDown(self):
        self.marks.discard()

    def path(self, name):
        return os.path.join(self.dir, name)

    def seed(self, name, text):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, name):
        with open(self.path(name), encoding="utf-8") as fh:
            return fh.read()

    def test_undo_restores_an_edited_file(self):
        self.seed("a.txt", "original\n")
        self.marks.begin_turn("change a")
        files.read(self.ctx, "a.txt")
        files.edit(self.ctx, "a.txt", "original", "modified")
        self.assertEqual(self.read("a.txt"), "modified\n")

        restored, deleted, failed = self.marks.undo()
        self.assertEqual(self.read("a.txt"), "original\n")
        self.assertEqual(restored, ["a.txt"])
        self.assertEqual((deleted, failed), ([], []))

    def test_undo_deletes_a_file_that_did_not_exist(self):
        self.marks.begin_turn("create b")
        files.write(self.ctx, "b.txt", "new file\n")
        self.assertTrue(os.path.exists(self.path("b.txt")))

        restored, deleted, _ = self.marks.undo()
        self.assertFalse(os.path.exists(self.path("b.txt")))
        self.assertEqual(deleted, ["b.txt"])
        self.assertEqual(restored, [])

    def test_undo_reverts_a_whole_turn_not_one_edit(self):
        self.seed("a.txt", "A\n")
        self.seed("b.txt", "B\n")
        self.marks.begin_turn("touch both")
        for name in ("a.txt", "b.txt"):
            files.read(self.ctx, name)
            files.edit(self.ctx, name, name[0].upper(), "changed")
        self.marks.undo()
        self.assertEqual(self.read("a.txt"), "A\n")
        self.assertEqual(self.read("b.txt"), "B\n")

    def test_undo_targets_only_the_named_turn(self):
        self.seed("a.txt", "v0\n")
        self.marks.begin_turn("first")
        files.read(self.ctx, "a.txt")
        files.edit(self.ctx, "a.txt", "v0", "v1")
        self.marks.begin_turn("second")
        files.read(self.ctx, "a.txt")
        files.edit(self.ctx, "a.txt", "v1", "v2")

        self.marks.undo()               # most recent turn only
        self.assertEqual(self.read("a.txt"), "v1\n")
        self.assertEqual(self.marks.turns(), [1])
        self.marks.undo()
        self.assertEqual(self.read("a.txt"), "v0\n")

    def test_multiple_edits_in_one_turn_restore_the_original(self):
        self.seed("a.txt", "start\n")
        self.marks.begin_turn("many edits")
        files.read(self.ctx, "a.txt")
        files.edit(self.ctx, "a.txt", "start", "middle")
        files.read(self.ctx, "a.txt")
        files.edit(self.ctx, "a.txt", "middle", "end")
        self.marks.undo()
        self.assertEqual(self.read("a.txt"), "start\n")

    def test_summary_reports_what_a_turn_touched(self):
        self.seed("a.txt", "x\n")
        self.marks.begin_turn("mixed")
        files.read(self.ctx, "a.txt")
        files.edit(self.ctx, "a.txt", "x", "y")
        files.write(self.ctx, "c.txt", "new\n")
        item = self.marks.summary(1)
        self.assertEqual(item["changed"], ["a.txt"])
        self.assertEqual(item["created"], ["c.txt"])

    def test_undo_with_nothing_recorded_is_harmless(self):
        self.assertEqual(self.marks.undo(), ([], [], []))


class SubAgentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ctx = make_ctx(self.dir)
        self.agent = Agent(config.load(), self.ctx)

    def test_agent_tool_is_registered(self):
        self.assertIn("agent", tools.REGISTRY)

    def test_context_exposes_spawn_to_tools(self):
        self.assertIsNotNone(self.ctx.spawn)

    def test_sub_agents_cannot_nest(self):
        """Depth is capped so a runaway cannot fork forever."""
        from ajna.agent import MAX_DEPTH
        child = Agent(config.load(), self.ctx.child(), depth=MAX_DEPTH - 1)
        names = {s["name"] for s in child._schemas()}
        self.assertNotIn("agent", names)

    def test_explore_role_has_no_mutating_tools(self):
        from ajna.tools.agents import ROLES
        for name in ROLES["explore"]["tools"]:
            self.assertFalse(tools.get(name).mutating,
                             "explore must not be able to %s" % name)
        for name in ROLES["review"]["tools"]:
            if name == "shell":
                continue        # review may run tests
            self.assertFalse(tools.get(name).mutating)

    def test_tool_filter_restricts_what_the_child_sees(self):
        child = Agent(config.load(), self.ctx.child(),
                      tool_filter={"read", "grep"}, depth=1)
        self.assertEqual({s["name"] for s in child._schemas()}, {"read", "grep"})

    def test_vague_task_is_refused(self):
        from ajna.tools.agents import agent as agent_tool
        result = agent_tool(self.ctx, task="look")
        self.assertTrue(result.error)

    def test_spawn_runs_a_child_and_returns_its_report(self):
        """Drive spawn with a fake client so the test needs no network."""
        class FakeClient:
            usage = {"in": 0, "out": 0, "requests": 0}

            def stream(self, messages, tools=None, model=None, cancel=None,
                       temperature=None, max_tokens=None):
                yield ("text", "Found it in src/auth.py:12.")

        self.agent.client = FakeClient()
        report, steps = self.agent.spawn(
            "Find where authentication happens in this project.",
            ("read", "grep"), "You are a research sub-agent.", "find auth")
        self.assertIn("src/auth.py:12", report)
        self.assertEqual(steps, 1)


class MCPTest(unittest.TestCase):
    def test_content_blocks_flatten_to_text(self):
        from ajna.mcp import _flatten_content
        self.assertEqual(
            _flatten_content({"content": [{"type": "text", "text": "hello"},
                                          {"type": "text", "text": "world"}]}),
            "hello\nworld")

    def test_resource_blocks_are_read(self):
        from ajna.mcp import _flatten_content
        out = _flatten_content({"content": [
            {"type": "resource", "resource": {"uri": "file://a", "text": "body"}}]})
        self.assertEqual(out, "body")

    def test_image_blocks_are_described_not_dropped(self):
        from ajna.mcp import _flatten_content
        out = _flatten_content({"content": [{"type": "image", "mimeType": "image/png"}]})
        self.assertIn("image", out)

    def test_missing_command_is_a_clean_error(self):
        from ajna.mcp import MCPError, Server
        with self.assertRaises(MCPError):
            Server("broken", {}).start()

    def test_a_real_stdio_server_round_trips(self):
        """Speak the protocol to a tiny server written for this test."""
        from ajna.mcp import Server

        script = os.path.join(tempfile.mkdtemp(), "server.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(FAKE_SERVER)

        server = Server("fake", {"command": sys.executable, "args": [script]})
        try:
            discovered = server.start(timeout=20)
            self.assertEqual([t["name"] for t in discovered], ["echo"])
            result = server.call("echo", {"text": "namaste"})
            self.assertEqual(result["content"][0]["text"], "echo: namaste")
        finally:
            server.stop()


FAKE_SERVER = '''
import json, sys

def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                 "result": result}) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        reply(msg["id"], {"protocolVersion": "2024-11-05",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "tools/list":
        reply(msg["id"], {"tools": [{
            "name": "echo",
            "description": "Echo the text back.",
            "inputSchema": {"type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"]}}]})
    elif method == "tools/call":
        text = msg["params"]["arguments"].get("text", "")
        reply(msg["id"], {"content": [{"type": "text", "text": "echo: " + text}]})
    elif msg.get("id") is not None:
        reply(msg["id"], {})
'''


if __name__ == "__main__":
    unittest.main(verbosity=2)
