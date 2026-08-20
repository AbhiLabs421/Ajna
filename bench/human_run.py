"""Run the benchmark against an agent that is not ajna.

`setup` lays out all ten task directories and prints the prompts. Another
agent — a person, Claude Code, anything — then does the work by hand. `verify`
scores the result with the same functions ajna is scored by, so the only thing
that differs is who did the work.

Free-text answers go in answers.json next to the task directories:

    {"find_answer": "...", "shell_count": "8", "no_op": "..."}
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import tasks as task_module        # noqa: E402


def setup(root):
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)

    print("Task directories under %s\n" % root)
    for task in task_module.TASKS:
        directory = os.path.join(root, task["name"])
        os.makedirs(directory)
        task["setup"](directory)
        print("=" * 70)
        print("%s  (%s)" % (task["name"], task["skill"]))
        if task.get("asks"):
            print("answer goes in answers.json under the key %r" % task["name"])
        print("-" * 70)
        print(task["prompt"])
        print()

    with open(os.path.join(root, "answers.json"), "w", encoding="utf-8") as fh:
        json.dump({t["name"]: "" for t in task_module.TASKS if t.get("asks")},
                  fh, indent=2)


def verify(root, label="agent"):
    try:
        with open(os.path.join(root, "answers.json"), encoding="utf-8") as fh:
            answers = json.load(fh)
    except (OSError, ValueError):
        answers = {}

    print("=" * 70)
    print("scoring %s" % label)
    print("=" * 70)

    passed = 0
    for task in task_module.TASKS:
        directory = os.path.join(root, task["name"])
        if not os.path.isdir(directory):
            print("  %-14s SKIP   directory missing" % task["name"])
            continue
        try:
            if task.get("asks"):
                ok, detail = task["verify"](directory, answers.get(task["name"], ""))
            else:
                ok, detail = task["verify"](directory)
        except Exception as exc:
            ok, detail = False, "verifier raised %r" % exc
        passed += bool(ok)
        print("  %-14s %-5s  %s" % (task["name"], "PASS" if ok else "FAIL",
                                    detail.replace("\n", " | ")[:110]))

    total = len(task_module.TASKS)
    print("=" * 70)
    print("  %d/%d passed  (%.0f%%)" % (passed, total, 100.0 * passed / total))
    print("=" * 70)
    return passed


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        print("usage: python bench/human_run.py setup|verify <directory>")
        raise SystemExit(2)
    action, root = sys.argv[1], os.path.abspath(sys.argv[2])
    if action == "setup":
        setup(root)
    elif action == "verify":
        verify(root, sys.argv[3] if len(sys.argv) > 3 else "agent")
    else:
        print("unknown action %r" % action)
        raise SystemExit(2)
