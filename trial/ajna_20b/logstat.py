#!/usr/bin/env python3
"""
Logstat: Summarise a web server access log.

Usage:
    python logstat.py <logfile> [--json] [--top N] [--status CODE] [--method M]

See SPEC.md for full specification.
"""

import argparse
import json
import sys
import re
from collections import Counter

LOG_PATTERN = re.compile(
    r"(?P<ip>\S+) \S+ \S+ \[(?P<timestamp>[^\]]+)\] \"(?P<method>\S+) (?P<path>\S+) \S+\" (?P<status>\d{3}) (?P<size>\S+)"
)


def parse_line(line: str):
    """Parse a single CLF line.
    Returns a dict with keys: ip, method, path, status, size (int).
    Raises ValueError if line is malformed.
    """
    m = LOG_PATTERN.match(line.strip())
    if not m:
        raise ValueError("Malformed line")
    size = m.group("size")
    size_val = 0 if size == "-" else int(size)
    return {
        "ip": m.group("ip"),
        "method": m.group("method"),
        "path": m.group("path"),
        "status": m.group("status"),
        "size": size_val,
    }


def process_log(filepath, status_filter=None, method_filter=None):
    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed_lines = 0

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = parse_line(line)
                except ValueError:
                    malformed_lines += 1
                    continue
                # Apply filters
                if status_filter and entry["status"] != status_filter:
                    continue
                if method_filter and entry["method"].lower() != method_filter.lower():
                    continue
                # Count
                total_requests += 1
                total_bytes += entry["size"]
                status_counts[entry["status"]] += 1
                ip_counts[entry["ip"]] += 1
                path_counts[entry["path"]] += 1
    except FileNotFoundError:
        print(f"Error: cannot read file '{filepath}'", file=sys.stderr)
        sys.exit(2)
    except PermissionError:
        print(f"Error: cannot read file '{filepath}'", file=sys.stderr)
        sys.exit(2)

    return {
        "total_requests": total_requests,
        "total_bytes": total_bytes,
        "status_counts": dict(status_counts),
        "ip_counts": ip_counts,
        "path_counts": path_counts,
        "malformed_lines": malformed_lines,
    }


def top_n(counter: Counter, n: int):
    # Return list of dicts sorted by count desc, then key asc
    return [
        {"ip" if k == "ip_counts" else "path": k, "count": v}
        for k, v in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:n]
    ]


def main():
    parser = argparse.ArgumentParser(description="Summarise a web server access log.")
    parser.add_argument("logfile", help="Path to the log file")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--top", type=int, default=5, help="Number of top entries to show")
    parser.add_argument("--status", help="Filter by status code")
    parser.add_argument("--method", help="Filter by HTTP method (case-insensitive)")
    args = parser.parse_args()

    result = process_log(args.logfile, status_filter=args.status, method_filter=args.method)

    if args.json:
        output = {
            "total_requests": result["total_requests"],
            "total_bytes": result["total_bytes"],
            "status_counts": result["status_counts"],
            "top_ips": sorted(
                [
                    {"ip": ip, "count": cnt}
                    for ip, cnt in result["ip_counts"].items()
                ],
                key=lambda d: (-d["count"], d["ip"]),
            )[: args.top],
            "top_paths": sorted(
                [
                    {"path": path, "count": cnt}
                    for path, cnt in result["path_counts"].items()
                ],
                key=lambda d: (-d["count"], d["path"]),
            )[: args.top],
            "malformed_lines": result["malformed_lines"],
        }
        print(json.dumps(output, indent=2))
    else:
        # Human readable
        print(f"Total requests: {result['total_requests']}")
        print(f"Total bytes: {result['total_bytes']}")
        print("Status counts:")
        for status, count in sorted(result["status_counts"].items()):
            print(f"  {status}: {count}")
        print("Top IPs:")
        for d in sorted(
            [{"ip": ip, "count": cnt} for ip, cnt in result["ip_counts"].items()],
            key=lambda d: (-d["count"], d["ip"]),
        )[: args.top]:
            print(f"  {d['ip']}: {d['count']}")
        print("Top paths:")
        for d in sorted(
            [{"path": path, "count": cnt} for path, cnt in result["path_counts"].items()],
            key=lambda d: (-d["count"], d["path"]),
        )[: args.top]:
            print(f"  {d['path']}: {d['count']}")
        print(f"Malformed lines: {result['malformed_lines']}")


if __name__ == "__main__":
    main()
