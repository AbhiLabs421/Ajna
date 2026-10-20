"""Tests for shell selection: whatever is chosen is the only shell used."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ajna import config, prompt, tools           # noqa: E402
from ajna.tools import shell                     # noqa: E402


def ctx_with_shell(kind):
    cfg = config.load()
    cfg["shell"] = kind
    return tools.Context(tempfile.mkdtemp(), cfg, permit=lambda *a, **k: True)


class ResolveTest(unittest.TestCase):
    def test_auto_resolves_to_a_real_shell(self):
        kind, executable, flags, note = shell.resolve("auto")
        self.assertIn(kind, shell.SHELL_SPECS)
        self.assertTrue(flags)
        self.assertTrue(note)

    def test_unknown_name_falls_back_rather_than_crashing(self):
        kind, _, _, _ = shell.resolve("fish-but-not-installed-here")
        self.assertEqual(kind, shell.default_kind())

    def test_available_only_lists_installed_shells(self):
        for kind in shell.available():
            _, executable, _, _ = shell.resolve(kind)
            self.assertIsNotNone(executable, "%s reported but not found" % kind)

    def test_each_spec_is_wellformed(self):
        for kind, (candidates, flags, note) in shell.SHELL_SPECS.items():
            self.assertTrue(candidates, kind)
            self.assertTrue(flags, kind)
            self.assertGreater(len(note), 20, kind)

    def test_describe_names_the_shell(self):
        text = shell.describe("cmd")
        self.assertTrue(text.startswith("cmd"))


class ArgvTest(unittest.TestCase):
    def test_cmd_uses_cmd_flags(self):
        argv, kind = shell._shell_command("echo hi", "cmd")
        if argv is None:
            self.skipTest("cmd.exe not present")
        self.assertEqual(kind, "cmd")
        # cmd is run from one verbatim string (not a list), so Python's
        # list2cmdline cannot mangle the command's own quotes.
        self.assertIsInstance(argv, str)
        self.assertIn("/c", argv)
        self.assertTrue(argv.endswith("echo hi"))

    def test_bash_uses_bash_flags(self):
        argv, kind = shell._shell_command("echo hi", "bash")
        if argv is None:
            self.skipTest("bash not present")
        self.assertEqual(kind, "bash")
        self.assertIn("-lc", argv)

    def test_powershell_uses_powershell_flags(self):
        argv, kind = shell._shell_command("echo hi", "powershell")
        if argv is None:
            self.skipTest("powershell not present")
        self.assertIn("-NoProfile", argv)


class OnlyChosenShellRunsTest(unittest.TestCase):
    """The point of the setting: pick cmd and nothing else is ever invoked."""

    def _run(self, kind, command):
        if kind not in shell.available():
            self.skipTest("%s not installed" % kind)
        return shell.shell(ctx_with_shell(kind), command)

    def test_cmd_runs_cmd_syntax(self):
        result = self._run("cmd", "echo %OS%")
        self.assertFalse(result.error, result.content)
        self.assertIn("Windows", result.content)

    def test_cmd_does_not_understand_powershell(self):
        """Proof it is really cmd: a cmdlet must fail there."""
        result = self._run("cmd", "Get-ChildItem")
        self.assertTrue(result.error, result.content)

    def test_bash_runs_posix_syntax(self):
        result = self._run("bash", "echo $((6*7))")
        self.assertFalse(result.error, result.content)
        self.assertIn("42", result.content)

    def test_powershell_runs_cmdlets(self):
        result = self._run("powershell", "Write-Output (6*7)")
        self.assertFalse(result.error, result.content)
        self.assertIn("42", result.content)

    def test_missing_shell_is_a_clear_message(self):
        ctx = ctx_with_shell("zsh")
        if "zsh" in shell.available():
            self.skipTest("zsh is installed here")
        result = shell.shell(ctx, "echo hi")
        self.assertTrue(result.error)
        self.assertIn("/shell", result.content)


class PromptTellsTheModelTest(unittest.TestCase):
    def test_system_prompt_names_the_chosen_shell(self):
        cfg = config.load()
        for kind in ("cmd", "bash", "powershell"):
            if kind not in shell.available():
                continue
            cfg["shell"] = kind
            text = prompt.build(os.getcwd(), cfg)
            self.assertIn("Shell: " + kind, text)

    def test_prompt_carries_syntax_guidance(self):
        cfg = config.load()
        if "cmd" not in shell.available():
            self.skipTest("cmd not installed")
        cfg["shell"] = "cmd"
        text = prompt.build(os.getcwd(), cfg)
        self.assertIn("cmd.exe", text)
        self.assertIn("%VAR%", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
