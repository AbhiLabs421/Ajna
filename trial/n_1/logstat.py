#!/usr/bin/env python3
import sys
import argparse
import json
import re
from collections import Counter, defaultdict

# Regular expression for Common Log Format
# Example: 127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
LOG_RE = re.compile(r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+\"(?P<method>\S+)\s+(?P<path>\S+)\s+\S+\"\s+(?P<status>\d{3})\s+(?P<size>\S+)$")


def parse_line(line: str):
    m = LOG_RE.match(line.strip())
    if not m:
        return None
    size = m.group('size')
    size_val = 0 if size == '-' else int(size)
    return {
        'ip': m.group('ip'),
        'method': m.group('method'),
        'path': m.group('path'),
        'status': m.group('status'),
        'size': size_val,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Summarise web server access log.')
    parser.add_argument('logfile', help='Path to log file')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    parser.add_argument('--top', type=int, default=5, help='Top N entries')
    parser.add_argument('--status', help='Filter by status code')
    parser.add_argument('--method', help='Filter by HTTP method (case-insensitive)')
    args = parser.parse_args(argv)

    try:
        f = open(args.logfile, 'r', encoding='utf-8')
    except Exception as e:
        sys.stderr.write(f"Error opening file {args.logfile}: {e}\n")
        sys.exit(2)

    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed_lines = 0

    for line in f:
        parsed = parse_line(line)
        if not parsed:
            malformed_lines += 1
            continue
        # Apply filters
        if args.status and parsed['status'] != args.status:
            continue
        if args.method and parsed['method'].lower() != args.method.lower():
            continue
        # Count
        total_requests += 1
        total_bytes += parsed['size']
        status_counts[parsed['status']] += 1
        ip_counts[parsed['ip']] += 1
        path_counts[parsed['path']] += 1

    f.close()

    top_n = args.top
    top_ips = sorted(ip_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    top_paths = sorted(path_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]

    if args.json:
        output = {
            'total_requests': total_requests,
            'total_bytes': total_bytes,
            'status_counts': {k: int(v) for k, v in status_counts.items()},
            'top_ips': [{'ip': ip, 'count': cnt} for ip, cnt in top_ips],
            'top_paths': [{'path': path, 'count': cnt} for path, cnt in top_paths],
            'malformed_lines': malformed_lines,
        }
        sys.stdout.write(json.dumps(output, indent=2))
        sys.stdout.write('\n')
    else:
        # Human readable
        sys.stdout.write(f"Total requests: {total_requests}\n")
        sys.stdout.write(f"Total bytes: {total_bytes}\n")
        sys.stdout.write("Status distribution:\n")
        for status, count in sorted(status_counts.items()):
            sys.stdout.write(f"  {status}: {count}\n")
        sys.stdout.write("Top IPs:\n")
        for ip, cnt in top_ips:
            sys.stdout.write(f"  {ip}: {cnt}\n")
        sys.stdout.write("Top paths:\n")
        for path, cnt in top_paths:
            sys.stdout.write(f"  {path}: {cnt}\n")
        sys.stdout.write(f"Malformed lines: {malformed_lines}\n")

if __name__ == '__main__':
    main()
