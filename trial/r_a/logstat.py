#!/usr/bin/env python3
"""
logstat.py: Summarise a web server access log.

Usage:
    python logstat.py <logfile> [--json] [--top N] [--status CODE] [--method M]

See SPEC.md for full specification.
"""

import argparse
import json
import sys
import re
from collections import Counter

# Regular expression for Common Log Format
LOG_PATTERN = re.compile(
    r"(?P<ip>\S+) \S+ \S+ \[(?P<time>[^]]+)\] \"(?P<method>\S+) (?P<path>\S+) \S+\" (?P<status>\d{3}) (?P<size>\S+)"
)


def parse_line(line: str):
    """Parse a single log line.

    Returns a dict with keys: ip, method, path, status, size (int).
    If the line is malformed, returns None.
    """
    m = LOG_PATTERN.match(line.strip())
    if not m:
        return None
    size = m.group('size')
    if size == '-':
        size_val = 0
    else:
        try:
            size_val = int(size)
        except ValueError:
            return None
    return {
        'ip': m.group('ip'),
        'method': m.group('method'),
        'path': m.group('path'),
        'status': m.group('status'),
        'size': size_val,
    }


def process_file(path, status_filter=None, method_filter=None, top_n=5):
    """Process the log file and return statistics.

    Returns a dict with keys: total_requests, total_bytes, status_counts,
    top_ips, top_paths, malformed_lines.
    """
    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed_lines = 0
    if path == '-':
        stream = sys.stdin
    else:
        stream = open(path, 'r', encoding='utf-8', errors='replace')
    try:
        for line in stream:
            parsed = parse_line(line)
            if not parsed:
                malformed_lines += 1
                continue
            if status_filter and parsed['status'] != status_filter:
                continue
            if method_filter and parsed['method'].lower() != method_filter.lower():
                continue
            total_requests += 1
            total_bytes += parsed['size']
            status_counts[parsed['status']] += 1
            ip_counts[parsed['ip']] += 1
            path_counts[parsed['path']] += 1
    finally:
        if stream is not sys.stdin:
            stream.close()

    # Build top lists
    def top_list(counter):
        return [
            {'ip' if k=='ip' else 'path': key, 'count': count}
            for key, count in sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:top_n]
        ]
    # Actually need separate for ip and path
    top_ips = [
        {'ip': key, 'count': count}
        for key, count in sorted(ip_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    ]
    top_paths = [
        {'path': key, 'count': count}
        for key, count in sorted(path_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    ]
    return {
        'total_requests': total_requests,
        'total_bytes': total_bytes,
        'status_counts': dict(status_counts),
        'top_ips': top_ips,
        'top_paths': top_paths,
        'malformed_lines': malformed_lines,
    }


def main():
    parser = argparse.ArgumentParser(description='Summarise a web server access log.')
    parser.add_argument('logfile', nargs='?', default='-', help='Path to the log file (use \'-\' for stdin)')
    parser.add_argument('--json', action='store_true', help='Emit JSON output')
    parser.add_argument('--top', type=int, default=5, help='Top N entries for IPs and paths')
    parser.add_argument('--status', help='Filter by status code')
    parser.add_argument('--method', help='Filter by HTTP method (case-insensitive)')
    args = parser.parse_args()

    try:
        stats = process_file(args.logfile, status_filter=args.status, method_filter=args.method, top_n=args.top)
    except (FileNotFoundError, PermissionError):
        sys.stderr.write(f"Error: cannot read file '{args.logfile}'.\n")
        sys.exit(2)

    if args.json:
        # Ensure JSON keys are strings as required
        json_obj = {
            'total_requests': stats['total_requests'],
            'total_bytes': stats['total_bytes'],
            'status_counts': {k: v for k, v in stats['status_counts'].items()},
            'top_ips': stats['top_ips'],
            'top_paths': stats['top_paths'],
            'malformed_lines': stats['malformed_lines'],
        }
        sys.stdout.write(json.dumps(json_obj, separators=(',', ':')) + '\n')
    else:
        # Human-readable output
        print(f"Total requests: {stats['total_requests']}")
        print(f"Total bytes: {stats['total_bytes']}")
        print("Status counts:")
        for status, count in sorted(stats['status_counts'].items()):
            print(f"  {status}: {count}")
        print("Top IPs:")
        for entry in stats['top_ips']:
            print(f"  {entry['ip']}: {entry['count']}")
        print("Top paths:")
        for entry in stats['top_paths']:
            print(f"  {entry['path']}: {entry['count']}")
        print(f"Malformed lines: {stats['malformed_lines']}")


if __name__ == '__main__':
    main()
