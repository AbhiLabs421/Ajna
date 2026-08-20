#!/usr/bin/env python3
"""Summarise a Common Log Format access log.

usage: logstat.py <logfile> [--json] [--top N] [--status CODE] [--method M]
"""

import argparse
import json
import re
import sys
from collections import Counter

# Anchored so a line with trailing junk is malformed rather than half-read.
# The request field is captured whole first, because real logs contain
# requests that are not three clean tokens ("GET /a b HTTP/1.1", or an empty
# string from a client that sent nothing).
LINE = re.compile(
    r'^(?P<ip>\S+) '
    r'(?P<identity>\S+) '
    r'(?P<user>\S+) '
    r'\[(?P<time>[^\]]*)\] '
    r'"(?P<request>[^"]*)" '
    r'(?P<status>\d{3}) '
    r'(?P<size>\d+|-)\s*$'
)


class Entry:
    __slots__ = ("ip", "method", "path", "status", "size")

    def __init__(self, ip, method, path, status, size):
        self.ip = ip
        self.method = method
        self.path = path
        self.status = status
        self.size = size


def parse_line(line):
    """Return an Entry, or None when the line is not a log line."""
    match = LINE.match(line.rstrip("\n"))
    if not match:
        return None
    parts = match.group("request").split()
    if len(parts) != 3:
        return None
    method, path, _protocol = parts
    size = match.group("size")
    return Entry(match.group("ip"), method, path, match.group("status"),
                 0 if size == "-" else int(size))


def read_log(handle):
    """Split a log into parsed entries and a count of unparseable lines."""
    entries, malformed = [], 0
    for line in handle:
        if not line.strip():
            continue          # blank lines are padding, not broken records
        entry = parse_line(line)
        if entry is None:
            malformed += 1
        else:
            entries.append(entry)
    return entries, malformed


def keep(entry, status=None, method=None):
    if status is not None and entry.status != status:
        return False
    if method is not None and entry.method.upper() != method.upper():
        return False
    return True


def rank(counter, key_name, limit):
    """Most frequent first; ties broken by name so output is reproducible."""
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{key_name: name, "count": count} for name, count in ordered[:limit]]


def summarise(entries, malformed, top=5):
    ips, paths, statuses = Counter(), Counter(), Counter()
    total_bytes = 0
    for entry in entries:
        ips[entry.ip] += 1
        paths[entry.path] += 1
        statuses[entry.status] += 1
        total_bytes += entry.size

    return {
        "total_requests": len(entries),
        "total_bytes": total_bytes,
        "status_counts": {code: statuses[code] for code in sorted(statuses)},
        "top_ips": rank(ips, "ip", top),
        "top_paths": rank(paths, "path", top),
        "malformed_lines": malformed,
    }


def render(summary, path):
    lines = [
        "%s" % path,
        "",
        "  requests   %d" % summary["total_requests"],
        "  bytes      %d" % summary["total_bytes"],
        "  malformed  %d" % summary["malformed_lines"],
    ]

    if summary["status_counts"]:
        lines += ["", "  status"]
        total = sum(summary["status_counts"].values())
        for code, count in summary["status_counts"].items():
            lines.append("    %-4s %6d  %5.1f%%" % (code, count,
                                                    100.0 * count / total))

    for title, key, field in (("top ips", "top_ips", "ip"),
                              ("top paths", "top_paths", "path")):
        if summary[key]:
            lines += ["", "  " + title]
            width = max(len(row[field]) for row in summary[key])
            for row in summary[key]:
                lines.append("    %-*s %6d" % (width, row[field], row["count"]))

    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="logstat", description="Summarise a Common Log Format access log.")
    parser.add_argument("logfile", help="path to the access log")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of a readable report")
    parser.add_argument("--top", type=int, default=5, metavar="N",
                        help="how many top ips and paths to show (default 5)")
    parser.add_argument("--status", metavar="CODE",
                        help="only count requests with this status code")
    parser.add_argument("--method", metavar="M",
                        help="only count requests with this method")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        with open(args.logfile, encoding="utf-8", errors="replace") as handle:
            entries, malformed = read_log(handle)
    except OSError as exc:
        # Nothing on stdout: a caller piping our JSON must not get half a
        # document followed by an error.
        sys.stderr.write("logstat: cannot read %s: %s\n"
                         % (args.logfile, exc.strerror or exc))
        return 2

    kept = [e for e in entries if keep(e, args.status, args.method)]
    summary = summarise(kept, malformed, top=max(0, args.top))

    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    else:
        sys.stdout.write(render(summary, args.logfile) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
