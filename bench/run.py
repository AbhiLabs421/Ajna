"""Benchmark runner.

    python bench/run.py                     all tasks, configured model
    python bench/run.py -m gpt-oss:20b      pick the model
    python bench/run.py fix_bug refactor    run named tasks only
    python bench/run.py --repeat 3          repeat for a stability estimate

Each task runs in a fresh temporary directory with `ajna --yolo -p`, then the
result is checked by running code. Nothing is scored on what the model claims.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import tasks as task_module           # noqa: E402


def run_task(task, model, timeout, keep=False):
    workdir = tempfile.mkdtemp(prefix="ajnabench-")
    started = time.time()
    record = {"name": task["name"], "skill": task["skill"]}

    try:
        task["setup"](workdir)

        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env["NO_COLOR"] = "1"
        argv = [sys.executable, "-m", "ajna", "-C", workdir, "--yolo",
                "-p", task["prompt"]]
        if model:
            argv[5:5] = ["-m", model]

        try:
            proc = subprocess.run(
                argv, env=env, timeout=timeout, cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            output = proc.stdout.decode("utf-8", "replace")
            crashed = proc.returncode not in (0, 1)
        except subprocess.TimeoutExpired:
            record.update(passed=False, seconds=timeout,
                          detail="agent exceeded %ds" % timeout)
            return record

        record["seconds"] = round(time.time() - started, 1)
        record["tool_calls"] = output.count("\n● ") + output.count("● ")

        if crashed:
            record.update(passed=False,
                          detail="ajna exited %d\n%s" % (proc.returncode,
                                                         output.strip()[-400:]))
            return record

        answer = _final_answer(output)
        if task.get("asks"):
            passed, detail = task["verify"](workdir, answer)
        else:
            passed, detail = task["verify"](workdir)
        record.update(passed=passed, detail=detail)
        return record

    except Exception as exc:
        record.update(passed=False, detail="harness error: %r" % exc)
        return record
    finally:
        if keep:
            record["dir"] = workdir
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _final_answer(output):
    """The agent's closing prose: everything after the last tool line."""
    lines = output.splitlines()
    last_tool = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("● ") or stripped.startswith("│"):
            last_tool = i
    return "\n".join(lines[last_tool + 1:]).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*", help="task names (default: all)")
    parser.add_argument("-m", "--model", default=None)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--keep", action="store_true",
                        help="leave the working directories in place")
    parser.add_argument("--json", default=None, help="write results here")
    args = parser.parse_args()

    selected = [t for t in task_module.TASKS
                if not args.names or t["name"] in args.names]
    if not selected:
        print("no task matched %s" % args.names)
        return 2

    model = args.model or "(configured default)"
    print("=" * 72)
    print("ajna benchmark — %d task%s x %d run%s — model: %s"
          % (len(selected), "" if len(selected) == 1 else "s",
             args.repeat, "" if args.repeat == 1 else "s", model))
    print("=" * 72)

    results = []
    for attempt in range(args.repeat):
        if args.repeat > 1:
            print("\n--- run %d of %d ---" % (attempt + 1, args.repeat))
        for task in selected:
            sys.stdout.write("  %-14s %-28s " % (task["name"], task["skill"]))
            sys.stdout.flush()
            record = run_task(task, args.model, args.timeout, args.keep)
            results.append(record)
            mark = "PASS" if record.get("passed") else "FAIL"
            print("%-5s %5ss  %s" % (mark, record.get("seconds", "?"),
                                     "" if record.get("passed")
                                     else record.get("detail", "")[:90]))

    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    seconds = sum(r.get("seconds", 0) for r in results)
    print("=" * 72)
    print("  %d/%d passed  (%.0f%%)   total %.0fs   mean %.1fs/task"
          % (passed, total, 100.0 * passed / total, seconds, seconds / total))
    print("=" * 72)

    if args.repeat > 1:
        print("\nper task:")
        for task in selected:
            runs = [r for r in results if r["name"] == task["name"]]
            wins = sum(1 for r in runs if r.get("passed"))
            print("  %-14s %d/%d" % (task["name"], wins, len(runs)))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"model": model, "results": results}, fh, indent=2)
        print("\nwrote %s" % args.json)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
