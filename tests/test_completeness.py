"""Tests for the two checks that catch a half-finished change.

Both were added because the benchmark caught the same failure twice: edit some
files, declare success, never look.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ajna import config, tools                   # noqa: E402
from ajna.agent import Agent                     # noqa: E402
from ajna.provider import ToolCall               # noqa: E402
from ajna.tools import Result, files             # noqa: E402


def make_ctx(root):
    return tools.Context(root, config.load(), permit=lambda *a, **k: True)


class DanglingReferenceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ctx = make_ctx(self.dir)
        self._write("core.py", "def compute_tax(amount):\n    return amount * 0.18\n")
        self._write("billing.py",
                    "from core import compute_tax\n\n\n"
                    "def invoice(a):\n    return a + compute_tax(a)\n")
        self._write("report.py",
                    "from core import compute_tax\n\n\n"
                    "def summary(a):\n    return compute_tax(a)\n")
        files.read(self.ctx, "core.py")

    def _write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_rename_reports_every_stale_call_site(self):
        result = files.edit(self.ctx, "core.py",
                            "def compute_tax(amount):", "def gst(amount):")
        self.assertFalse(result.error, result.content)
        self.assertIn("still referenced elsewhere", result.content)
        self.assertIn("billing.py", result.content)
        self.assertIn("report.py", result.content)

    def test_no_warning_when_nothing_dangles(self):
        self._write("lonely.py", "def only_here():\n    return 1\n")
        files.read(self.ctx, "lonely.py")
        result = files.edit(self.ctx, "lonely.py",
                            "def only_here():", "def renamed():")
        self.assertNotIn("still referenced elsewhere", result.content)

    def test_the_edited_file_is_not_reported_against_itself(self):
        self._write("solo.py", "def helper():\n    return 1\n\n\nx = helper()\n")
        files.read(self.ctx, "solo.py")
        result = files.edit(self.ctx, "solo.py", "def helper():", "def aide():")
        self.assertNotIn("solo.py:", result.content)

    def test_short_names_are_not_hunted(self):
        """Renaming `f` must not match every f in the tree."""
        self._write("tiny.py", "def f():\n    return 1\n")
        files.read(self.ctx, "tiny.py")
        result = files.edit(self.ctx, "tiny.py", "def f():", "def g():")
        self.assertNotIn("still referenced elsewhere", result.content)


class NudgeTest(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(config.load(), make_ctx(tempfile.mkdtemp()))
        self.agent._touched = []
        self.agent._checked = True
        self.agent._nudged = 0

    def _seed_test_file(self):
        """The test-run nudge only fires for projects that have tests."""
        with open(os.path.join(self.agent.ctx.root, "test_x.py"), "w") as fh:
            fh.write("")

    def _effect(self, name, args, error=False):
        call = ToolCall("id", name, args)
        self.agent._note_tool_effect(call, Result("done", error=error))

    def test_editing_then_stopping_asks_for_a_test_run(self):
        self._seed_test_file()
        self._effect("edit", {"path": "a.py"})
        nudge = self.agent._completeness_nudge()
        self.assertIsNotNone(nudge)
        self.assertIn("a.py", nudge)
        self.assertIn("Do not edit", nudge)

    def test_no_nudge_when_the_project_has_no_tests(self):
        self._effect("edit", {"path": "a.py"})
        self.assertIsNone(self.agent._completeness_nudge())

    def test_the_generic_nudge_fires_only_once_per_turn(self):
        self._seed_test_file()
        self._effect("edit", {"path": "a.py"})
        self.assertIsNotNone(self.agent._completeness_nudge())
        self.assertIsNone(self.agent._completeness_nudge())

    def test_running_a_command_after_editing_satisfies_it(self):
        self._seed_test_file()
        self._effect("edit", {"path": "a.py"})
        self._effect("shell", {"command": "python -m unittest"})
        self.assertIsNone(self.agent._completeness_nudge())

    def test_grep_after_editing_satisfies_it(self):
        self._seed_test_file()
        self._effect("write", {"path": "a.py"})
        self._effect("grep", {"pattern": "old_name"})
        self.assertIsNone(self.agent._completeness_nudge())

    def test_rereading_the_file_does_not_satisfy_it(self):
        """Reading back what you just wrote proves nothing about the project."""
        self._seed_test_file()
        self._effect("edit", {"path": "a.py"})
        self._effect("read", {"path": "a.py"})
        self.assertIsNotNone(self.agent._completeness_nudge())

    def test_a_read_only_turn_is_never_nudged(self):
        self._effect("read", {"path": "a.py"})
        self._effect("grep", {"pattern": "x"})
        self.assertIsNone(self.agent._completeness_nudge())

    def test_a_failed_edit_does_not_count_as_a_change(self):
        self._effect("edit", {"path": "a.py"}, error=True)
        self.assertIsNone(self.agent._completeness_nudge())

    def test_editing_again_after_a_check_re_arms_it(self):
        self._seed_test_file()
        self._effect("edit", {"path": "a.py"})
        self._effect("shell", {"command": "pytest"})
        self._effect("edit", {"path": "b.py"})
        nudge = self.agent._completeness_nudge()
        self.assertIsNotNone(nudge)
        self.assertIn("b.py", nudge)


if __name__ == "__main__":
    unittest.main(verbosity=2)
