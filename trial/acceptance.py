"""Acceptance tests for logstat, written against SPEC.md alone.

Run against any implementation directory:

    python trial/acceptance.py <dir>

Nothing here inspects the implementation. Everything goes through the command
line the spec defines, so the same tests judge every candidate identically.
"""

import json
import os
import subprocess
import sys

LOG = """\
10.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
10.0.0.2 - - [10/Oct/2023:13:55:37 +0000] "GET /index.html HTTP/1.1" 200 2326
10.0.0.1 - - [10/Oct/2023:13:55:38 +0000] "POST /login HTTP/1.1" 302 0
this line is not a log line at all
10.0.0.3 - - [10/Oct/2023:13:56:01 +0000] "GET /missing HTTP/1.1" 404 512
10.0.0.1 - - [10/Oct/2023:13:56:02 +0000] "GET /style.css HTTP/1.1" 200 1024
10.0.0.2 - - [10/Oct/2023:13:56:03 +0000] "DELETE /admin HTTP/1.1" 500 128
10.0.0.4 - - [10/Oct/2023:13:56:04 +0000] "GET /index.html HTTP/1.1" 200 -
<<< garbage >>>
10.0.0.2 - - [10/Oct/2023:13:56:05 +0000] "GET /missing HTTP/1.1" 404 512
"""

# Derived by hand from LOG and SPEC.md.
#   well-formed: 8   malformed: 2
#   bytes: 2326+2326+0+512+1024+128+0+512 = 6828
#   ips: 10.0.0.1 x3, 10.0.0.2 x3, 10.0.0.3 x1, 10.0.0.4 x1
#   paths: /index.html x3, /missing x2, /login x1, /style.css x1, /admin x1
#   status: 200 x4, 302 x1, 404 x2, 500 x1

# Every check this suite can make. A run that dies early must still be scored
# out of the full total — reporting "27/30" and "34/34" as 90% and 100% flatters
# the broken run, because the checks it never reached were the hard ones.
ALL_CHECKS = [
    "json runs and parses",
    "total_requests counts only well-formed lines",
    "total_bytes sums sizes, '-' as zero",
    "malformed_lines counted",
    "status_counts correct",
    "status_counts keys are strings",
    "top_ips ordered, ties broken by ip ascending",
    "top_paths ordered, ties broken by path ascending",
    "--top runs",
    "--top limits top_ips",
    "--top limits top_paths",
    "--top does not change totals",
    "--status runs",
    "--status filters requests",
    "--status filters bytes",
    "--status narrows status_counts",
    "--status leaves malformed count alone",
    "--method runs and is case-insensitive",
    "--method filters requests",
    "filters combine",
    "--method with --status is an AND",
    "empty log exits 0",
    "empty log reports zeros",
    "empty log still has every key",
    "missing file exits 2",
    "missing file writes to stderr",
    "missing file writes nothing to stdout",
    "plain run exits 0",
    "report mentions the total",
    "report mentions top ips",
    "report mentions paths",
    "report is not just the JSON blob",
    "ships its own tests",
    "its own tests pass",
]

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    return bool(condition)


def run(directory, *args, log=None):
    argv = [sys.executable, "logstat.py"] + list(args)
    try:
        proc = subprocess.run(argv, cwd=directory, timeout=30,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 99, "", str(exc)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def run_json(directory, *args):
    code, out, err = run(directory, *args)
    if code != 0:
        return None, "exit %d, stderr: %s" % (code, err.strip()[:200])
    try:
        return json.loads(out), ""
    except ValueError as exc:
        return None, "stdout is not JSON (%s): %r" % (exc, out.strip()[:200])


def main(directory):
    directory = os.path.abspath(directory)
    if not os.path.isfile(os.path.join(directory, "logstat.py")):
        print("no logstat.py in %s" % directory)
        return 1

    with open(os.path.join(directory, "access.log"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(LOG)
    with open(os.path.join(directory, "empty.log"), "w", encoding="utf-8") as fh:
        fh.write("")

    # --- the core summary ------------------------------------------------
    data, why = run_json(directory, "access.log", "--json")
    if not check("json runs and parses", data is not None, why):
        report()
        return 1

    check("total_requests counts only well-formed lines",
          data.get("total_requests") == 8, "got %r" % data.get("total_requests"))
    check("total_bytes sums sizes, '-' as zero",
          data.get("total_bytes") == 6828, "got %r" % data.get("total_bytes"))
    check("malformed_lines counted",
          data.get("malformed_lines") == 2, "got %r" % data.get("malformed_lines"))
    check("status_counts correct",
          data.get("status_counts") == {"200": 4, "302": 1, "404": 2, "500": 1},
          "got %r" % data.get("status_counts"))
    check("status_counts keys are strings",
          all(isinstance(k, str) for k in (data.get("status_counts") or {})),
          "got %r" % list((data.get("status_counts") or {}).keys()))

    # --- ordering and tie-breaking ---------------------------------------
    check("top_ips ordered, ties broken by ip ascending",
          data.get("top_ips") == [
              {"ip": "10.0.0.1", "count": 3},
              {"ip": "10.0.0.2", "count": 3},
              {"ip": "10.0.0.3", "count": 1},
              {"ip": "10.0.0.4", "count": 1},
          ], "got %r" % data.get("top_ips"))
    check("top_paths ordered, ties broken by path ascending",
          data.get("top_paths") == [
              {"path": "/index.html", "count": 3},
              {"path": "/missing", "count": 2},
              {"path": "/admin", "count": 1},
              {"path": "/login", "count": 1},
              {"path": "/style.css", "count": 1},
          ], "got %r" % data.get("top_paths"))

    # --- --top -----------------------------------------------------------
    data2, why = run_json(directory, "access.log", "--json", "--top", "2")
    if check("--top runs", data2 is not None, why):
        check("--top limits top_ips", len(data2.get("top_ips") or []) == 2,
              "got %d" % len(data2.get("top_ips") or []))
        check("--top limits top_paths", len(data2.get("top_paths") or []) == 2,
              "got %d" % len(data2.get("top_paths") or []))
        check("--top does not change totals",
              data2.get("total_requests") == 8, "got %r" % data2.get("total_requests"))

    # --- --status --------------------------------------------------------
    data3, why = run_json(directory, "access.log", "--json", "--status", "404")
    if check("--status runs", data3 is not None, why):
        check("--status filters requests", data3.get("total_requests") == 2,
              "got %r" % data3.get("total_requests"))
        check("--status filters bytes", data3.get("total_bytes") == 1024,
              "got %r" % data3.get("total_bytes"))
        check("--status narrows status_counts",
              data3.get("status_counts") == {"404": 2},
              "got %r" % data3.get("status_counts"))
        check("--status leaves malformed count alone",
              data3.get("malformed_lines") == 2,
              "got %r" % data3.get("malformed_lines"))

    # --- --method --------------------------------------------------------
    data4, why = run_json(directory, "access.log", "--json", "--method", "get")
    if check("--method runs and is case-insensitive", data4 is not None, why):
        check("--method filters requests", data4.get("total_requests") == 6,
              "got %r" % data4.get("total_requests"))

    # --- filters combine -------------------------------------------------
    data5, why = run_json(directory, "access.log", "--json",
                          "--method", "GET", "--status", "200")
    if check("filters combine", data5 is not None, why):
        check("--method with --status is an AND",
              data5.get("total_requests") == 4,
              "got %r" % data5.get("total_requests"))

    # --- empty log -------------------------------------------------------
    data6, why = run_json(directory, "empty.log", "--json")
    if check("empty log exits 0", data6 is not None, why):
        check("empty log reports zeros",
              data6.get("total_requests") == 0 and data6.get("total_bytes") == 0,
              "got %r" % data6)
        check("empty log still has every key",
              all(k in data6 for k in ("total_requests", "total_bytes",
                                       "status_counts", "top_ips",
                                       "top_paths", "malformed_lines")),
              "got keys %r" % sorted(data6))

    # --- missing file ----------------------------------------------------
    code, out, err = run(directory, "nope.log", "--json")
    check("missing file exits 2", code == 2, "got exit %r" % code)
    check("missing file writes to stderr", err.strip() != "", "stderr was empty")
    check("missing file writes nothing to stdout", out.strip() == "",
          "stdout was %r" % out.strip()[:120])

    # --- human-readable output -------------------------------------------
    code, out, err = run(directory, "access.log")
    if check("plain run exits 0", code == 0, "exit %r, stderr %s" % (code, err[:150])):
        low = out.lower()
        check("report mentions the total", "8" in out, "output was %r" % out[:200])
        check("report mentions top ips", "10.0.0.1" in out)
        check("report mentions paths", "/index.html" in out)
        check("report is not just the JSON blob",
              not out.strip().startswith("{"), "looks like raw JSON")

    # --- the project's own tests -----------------------------------------
    has_tests = any(n.startswith("test") and n.endswith(".py")
                    for n in os.listdir(directory))
    if check("ships its own tests", has_tests, "no test_*.py found"):
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover"],
                              cwd=directory, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=120)
        body = proc.stdout.decode("utf-8", "replace")
        check("its own tests pass", proc.returncode == 0,
              body.strip().splitlines()[-1] if body.strip() else "no output")

    return report()


def report():
    seen = {name: (ok, detail) for name, ok, detail in RESULTS}
    passed = 0
    for name in ALL_CHECKS:
        if name in seen:
            ok, detail = seen[name]
            passed += bool(ok)
            print("  %-52s %s%s" % (name, "PASS" if ok else "FAIL",
                                    "" if ok else "  " + detail[:90]))
        else:
            print("  %-52s %s" % (name, "FAIL  not reached"))

    stray = [n for n, _, _ in RESULTS if n not in set(ALL_CHECKS)]
    if stray:
        print("  (ALL_CHECKS is out of date, missing: %s)" % ", ".join(stray))

    total = len(ALL_CHECKS)
    print("  " + "-" * 66)
    print("  %d/%d  (%.0f%%)" % (passed, total, 100.0 * passed / total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python trial/acceptance.py <implementation-dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
