"""Differential testing: feed every implementation the same nasty input.

The acceptance suite proves the spec is met. This asks a harder question —
what happens at the edges the spec never mentioned, where an implementation
has to fall back on judgement. Disagreement between implementations is the
signal; who is right is then a decision, not a measurement.
"""

import json
import os
import subprocess
import sys

GOOD = ('10.0.0.1 - - [10/Oct/2023:13:55:36 +0000] '
        '"GET /index.html HTTP/1.1" 200 2326')

CASES = [
    ("request containing quotes",
     '10.0.0.1 - - [t] "GET /a?q=\\"x\\" HTTP/1.1" 200 10\n' + GOOD, []),
    ("request with only two tokens",
     '10.0.0.1 - - [t] "GET /nope" 200 10\n' + GOOD, []),
    ("empty request string",
     '10.0.0.1 - - [t] "" 200 10\n' + GOOD, []),
    ("trailing junk after the size",
     GOOD + " EXTRA\n" + GOOD, []),
    ("two-digit status code",
     '10.0.0.1 - - [t] "GET /a HTTP/1.1" 20 10\n' + GOOD, []),
    ("negative size",
     '10.0.0.1 - - [t] "GET /a HTTP/1.1" 200 -5\n' + GOOD, []),
    ("crlf line endings", GOOD.replace("\n", "") + "\r\n" + GOOD + "\r\n", []),
    ("blank lines between records", GOOD + "\n\n\n" + GOOD + "\n", []),
    ("no trailing newline", GOOD, []),
    ("unicode in the path",
     '10.0.0.1 - - [t] "GET /café/मेनू HTTP/1.1" 200 10\n', []),
    ("very long path",
     '10.0.0.1 - - [t] "GET /%s HTTP/1.1" 200 10\n' % ("a" * 5000), []),
    ("huge byte count",
     '10.0.0.1 - - [t] "GET /a HTTP/1.1" 200 99999999999999999999\n', []),
    ("ipv6 client address",
     '2001:db8::1 - - [t] "GET /a HTTP/1.1" 200 10\n' + GOOD, []),
    ("--top 0", GOOD + "\n" + GOOD, ["--top", "0"]),
    ("--top negative", GOOD + "\n", ["--top", "-3"]),
    ("--top larger than the data", GOOD + "\n", ["--top", "999"]),
    ("--status that matches nothing", GOOD + "\n", ["--status", "418"]),
    ("--status non-numeric", GOOD + "\n", ["--status", "abc"]),
    ("--method that matches nothing", GOOD + "\n", ["--method", "TRACE"]),
    ("--method mixed case", GOOD + "\n", ["--method", "GeT"]),
    ("log of only malformed lines", "garbage\nmore garbage\n", []),
    ("invalid utf-8 bytes", None, []),          # written as raw bytes below
]


def run(directory, log_name, extra):
    argv = [sys.executable, "logstat.py", log_name, "--json"] + list(extra)
    try:
        proc = subprocess.run(argv, cwd=directory, timeout=30,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"outcome": "TIMEOUT"}
    except OSError as exc:
        return {"outcome": "CANNOT RUN: %s" % exc}

    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()

    if proc.returncode != 0:
        first = err.splitlines()[-1] if err else "(silent)"
        kind = "CRASH" if "Traceback" in err else "exit %d" % proc.returncode
        return {"outcome": "%s: %s" % (kind, first[:70])}
    try:
        data = json.loads(out)
    except ValueError:
        return {"outcome": "exit 0 but stdout is not JSON"}
    return {
        "outcome": "ok",
        "requests": data.get("total_requests"),
        "bytes": data.get("total_bytes"),
        "malformed": data.get("malformed_lines"),
        "ips": len(data.get("top_ips") or []),
    }


def summarise(result):
    if result["outcome"] != "ok":
        return result["outcome"]
    return "req=%s bytes=%s bad=%s ips=%s" % (
        result["requests"], result["bytes"], result["malformed"], result["ips"])


def main(dirs):
    names = [os.path.basename(d.rstrip("/\\")) for d in dirs]
    width = max(len(n) for n in names)
    disagreements = 0

    print("=" * 100)
    print("differential testing: %s" % ", ".join(names))
    print("=" * 100)

    for label, body, extra in CASES:
        log_name = "diff.log"
        for directory in dirs:
            path = os.path.join(directory, log_name)
            if body is None:
                with open(path, "wb") as fh:
                    fh.write(b'10.0.0.1 - - [t] "GET /\xff\xfe HTTP/1.1" 200 10\n')
            else:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(body)

        outputs = [summarise(run(d, log_name, extra)) for d in dirs]
        agree = len(set(outputs)) == 1
        if not agree:
            disagreements += 1

        print("\n%s %s" % ("  " if agree else "!!", label))
        for name, text in zip(names, outputs):
            print("     %-*s  %s" % (width, name, text))

    for directory in dirs:
        try:
            os.remove(os.path.join(directory, "diff.log"))
        except OSError:
            pass

    print("\n" + "=" * 100)
    print("  %d of %d cases produced different behaviour" % (disagreements, len(CASES)))
    print("=" * 100)
    return disagreements


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python trial/differential.py <dir> <dir> [dir...]")
        raise SystemExit(2)
    main(sys.argv[1:])
