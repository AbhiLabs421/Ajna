#!/usr/bin/env python3
"""A scripted demo of ajna fixing a real bug, meant for screen recording.

It seeds a tiny throwaway project with a failing test, shows the failure, lets
ajna find and fix the bug on its own, then shows the test passing. Nothing in
your real working tree is touched -- the sample lives in a temp directory.

    python scripts/demo.py

Requires a configured provider ( `ajna --setup` ) since ajna calls a model to
do the work. Set DEMO_PAUSE to change the pacing (seconds, default 1.2); set
DEMO_PAUSE=0 for an un-paced run.
"""

import os
import subprocess
import sys
import tempfile
import time

PAUSE = float(os.environ.get("DEMO_PAUSE", "1.2"))
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


def pause(mult=1.0):
    if PAUSE:
        time.sleep(PAUSE * mult)


def banner(text):
    print()
    print(c("  " + text, "1;36"))
    print(c("  " + "-" * len(text), "36"))
    pause()


def prompt(line):
    """Print a shell line the way a person would type it, then a beat."""
    print(c("  $ ", "1;32") + c(line, "1"))
    pause()


SAMPLE = {
    "stats.py": (
        '"""Small statistics helpers."""\n\n\n'
        "def median(values):\n"
        '    """Return the median of a list of numbers."""\n'
        "    ordered = sorted(values)\n"
        "    middle = len(ordered) // 2\n"
        "    return ordered[middle]\n"
    ),
    "test_stats.py": (
        "import unittest\n\n"
        "from stats import median\n\n\n"
        "class MedianTest(unittest.TestCase):\n"
        "    def test_odd_length(self):\n"
        "        self.assertEqual(median([3, 1, 2]), 2)\n\n"
        "    def test_even_length(self):\n"
        "        # the average of the two middle values\n"
        "        self.assertEqual(median([1, 2, 3, 4]), 2.5)\n\n\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    ),
}


def seed(project):
    for name, body in SAMPLE.items():
        with open(os.path.join(project, name), "w", encoding="utf-8") as handle:
            handle.write(body)


def run_tests(project):
    return subprocess.run(
        [sys.executable, "-m", "unittest", "-q"],
        cwd=project,
    ).returncode


def run_ajna(project, task):
    # Drive the same ajna that ships in this repo, pointed at the sample dir.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "ajna", "-C", project, "--yolo", "-p", task],
        env=env,
    ).returncode


def main():
    project = tempfile.mkdtemp(prefix="ajna-demo-")
    seed(project)

    banner("1. A small project with a failing test")
    prompt("python -m unittest")
    pause()
    rc = run_tests(project)
    if rc == 0:
        print(c("  (expected a failure here; check the sample)", "33"))
    pause(1.5)

    banner("2. Hand the bug to ajna -- no hints")
    task = "A test in this folder is failing. Find the bug and fix it."
    prompt('ajna --yolo -p "%s"' % task)
    pause()
    run_ajna(project, task)
    pause(1.5)

    banner("3. Run the test again")
    prompt("python -m unittest")
    pause()
    rc = run_tests(project)
    print()
    if rc == 0:
        print(c("  Fixed. The agent read the code, found the bug, and "
                "edited the file -- on its own.", "1;32"))
    else:
        print(c("  The test is still failing; try again or check your "
                "model configuration.", "1;33"))
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
