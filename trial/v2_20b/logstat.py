#!/usr/bin/env python3
"""
logstat.py – summarize a web server access log in Common Log Format.

Usage:
    python logstat.py <logfile> [--json] [--top N] [--status CODE] [--method M]

See SPEC.md for full specification.
"""

import argparse
import json
import re
import sys
from collections import Counter

# Regular expression for Common Log Format
# Example: 127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
LOG_RE = re.compile(r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+\"(?P<method>\S+)\s+(?P<path>\S+)\s+\S+\"\s+(?P<status>\d{3})\s+(?P<size>\S+)$")


def parse_line(line: str):
    """Parse a single log line.

    Returns a dict with keys: ip, time, method, path, status, size.
    Raises ValueError if line does not match CLF.
    """
    m = LOG_RE.match(line.rstrip("\n"))
    if not m:
        raise ValueError("Malformed line")
    d = m.groupdict()
    # Convert size
    size = d["size"]
    if size == "-":
        size = 0
    else:
        size = int(size)
    d["size"] = size
    d["status"] = d["status"]
    return d


def main(argv=None):
    parser = argparse.ArgumentParser(description="Summarize a web server access log.")
    parser.add_argument("logfile", help="Path to the log file")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--top", type=int, default=5, help="Number of top IPs and paths to show")
    parser.add_argument("--status", help="Filter by status code")
    parser.add_argument("--method", help="Filter by HTTP method (case-insensitive)")
    args = parser.parse_args(argv)

    try:
        f = open(args.logfile, "r", encoding="utf-8")
    except OSError as e:
        print(f"Error: cannot read file {args.logfile}: {e}", file=sys.stderr)
        sys.exit(2)

    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed_lines = 0

    status_filter = args.status
    method_filter = args.method.lower() if args.method else None

    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            d = parse_line(line)
        except ValueError:
            malformed_lines += 1
            continue
        # Apply filters
        if status_filter and d["status"] != status_filter:
            continue
        if method_filter and d["method"].lower() != method_filter:
            continue
        # Count
        total_requests += 1
        total_bytes += d["size"]
        status_counts[d["status"]] += 1
        ip_counts[d["ip"]] += 1
        path_counts[d["path"]] += 1

    f.close()

    # Prepare top lists
    def top_counter(counter):
        return sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:args.top]

    top_ips = [{"ip": ip, "count": cnt} for ip, cnt in top_counter(ip_counts)]
    top_paths = [{"path": path, "count": cnt} for path, cnt in top_counter(path_counts)]

    if args.json:
        out = {
            "total_requests": total_requests,
            "total_bytes": total_bytes,
            "status_counts": {k: v for k, v in status_counts.items()},
            "top_ips": top_ips,
            "top_paths": top_paths,
            "malformed_lines": malformed_lines,
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"Total requests: {total_requests}")
        print(f"Total bytes: {total_bytes}")
        print("Status distribution:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")
        print("Top IPs:")
        for entry in top_ips:
            print(f"  {entry['ip']}: {entry['count']}")
        print("Top paths:")
        for entry in top_paths:
            print(f"  {entry['path']}: {entry['count']}")
        print(f"Malformed lines: {malformed_lines}")


if __name__ == "__main__":
    main()
