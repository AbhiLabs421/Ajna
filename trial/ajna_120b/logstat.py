#!/usr/bin/env python3
"""logstat.py – summarise a web server access log (Common Log Format).

Usage:
    python logstat.py <logfile> [--json] [--top N] [--status CODE] [--method M]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Regex for Common Log Format line
# Example: 127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
CLF_RE = re.compile(r'''(?P<ip>\S+)\s+          # IP
    \S+\s+\S+\s+                     # identity and user (ignored)
    \[(?P<time>[^\]]+)\]\s+          # timestamp
    "(?P<method>\S+)\s+(?P<path>\S+)\s+(?P<proto>\S+)"\s+  # request line
    (?P<status>\d{3})\s+               # status code
    (?P<size>\S+)\s*                  # size (or -)
    ''', re.VERBOSE)

def parse_line(line: str):
    """Parse a CLF line.
    Return a dict with keys ip, method, path, status, size (int) or None if malformed.
    """
    m = CLF_RE.match(line.strip())
    if not m:
        return None
    size_str = m.group('size')
    size = 0 if size_str == '-' else int(size_str)
    return {
        'ip': m.group('ip'),
        'method': m.group('method'),
        'path': m.group('path'),
        'status': m.group('status'),
        'size': size,
    }

def main():
    parser = argparse.ArgumentParser(description='Summarise a web server access log.')
    parser.add_argument('logfile', help='Path to the log file')
    parser.add_argument('--json', action='store_true', help='Emit JSON output')
    parser.add_argument('--top', type=int, default=5, help='Number of top entries to show')
    parser.add_argument('--status', help='Filter by HTTP status code')
    parser.add_argument('--method', help='Filter by HTTP method (case-insensitive)')
    args = parser.parse_args()

    log_path = Path(args.logfile)
    if not log_path.is_file():
        print(f"Error: cannot read log file '{args.logfile}'", file=sys.stderr)
        sys.exit(2)

    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed = 0

    method_filter = args.method.upper() if args.method else None
    status_filter = args.status if args.status else None

    try:
        with log_path.open('r', encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.rstrip('\n')
                parsed = parse_line(line)
                if parsed is None:
                    malformed += 1
                    continue
                # Apply filters before counting
                if method_filter and parsed['method'].upper() != method_filter:
                    continue
                if status_filter and parsed['status'] != status_filter:
                    continue
                # Count
                total_requests += 1
                total_bytes += parsed['size']
                status_counts[parsed['status']] += 1
                ip_counts[parsed['ip']] += 1
                path_counts[parsed['path']] += 1
    except Exception as e:
        print(f"Error reading log file: {e}", file=sys.stderr)
        sys.exit(2)

    # Prepare top lists with deterministic tie-breaking
    def top_items(counter, key_name):
        items = list(counter.items())
        items.sort(key=lambda kv: (-kv[1], kv[0]))
        top_n = items[:args.top]
        return [{key_name: k, 'count': v} for k, v in top_n]

    result = {
        'total_requests': total_requests,
        'total_bytes': total_bytes,
        'status_counts': {k: v for k, v in sorted(status_counts.items())},
        'top_ips': top_items(ip_counts, 'ip'),
        'top_paths': top_items(path_counts, 'path'),
        'malformed_lines': malformed,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        # Human‑readable report
        print(f"Total requests: {total_requests}")
        print(f"Total bytes: {total_bytes}")
        print("Status counts:")
        for code, cnt in sorted(status_counts.items()):
            print(f"  {code}: {cnt}")
        print("Top IPs:")
        for entry in result['top_ips']:
            print(f"  {entry['ip']}: {entry['count']}")
        print("Top paths:")
        for entry in result['top_paths']:
            print(f"  {entry['path']}: {entry['count']}")
        print(f"Malformed lines: {malformed}")

if __name__ == '__main__':
    main()
