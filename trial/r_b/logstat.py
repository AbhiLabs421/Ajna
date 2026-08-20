#!/usr/bin/env python3
import sys
import argparse
import json
import re
from collections import Counter

# Regular expression for Common Log Format
# Example: 127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
LOG_RE = re.compile(r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+\"(?P<method>\S+)\s+(?P<path>\S+)\s+\S+\"\s+(?P<status>\d{3})\s+(?P<size>\S+)$")


def parse_line(line):
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


def process_file(path, status_filter=None, method_filter=None):
    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed_lines = 0

    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                parsed = parse_line(line)
                if not parsed:
                    malformed_lines += 1
                    continue
                # Apply filters
                if status_filter and parsed['status'] != status_filter:
                    continue
                if method_filter and parsed['method'].lower() != method_filter.lower():
                    continue
                # Count
                total_requests += 1
                total_bytes += parsed['size']
                status_counts[parsed['status']] += 1
                ip_counts[parsed['ip']] += 1
                path_counts[parsed['path']] += 1
    except OSError as e:
        sys.stderr.write(f"Error reading file {path}: {e}\n")
        sys.exit(2)

    # Prepare top lists
    def top_list(counter):
        items = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
        return [{'ip' if 'ip' in k else 'path': k, 'count': v} for k, v in items]

    top_ips = top_list(ip_counts)[:args.top]
    top_paths = top_list(path_counts)[:args.top]

    return {
        'total_requests': total_requests,
        'total_bytes': total_bytes,
        'status_counts': dict(status_counts),
        'top_ips': top_ips,
        'top_paths': top_paths,
        'malformed_lines': malformed_lines,
    }


def main():
    parser = argparse.ArgumentParser(description='Summarise web server access log.')
    parser.add_argument('logfile')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--top', type=int, default=5)
    parser.add_argument('--status')
    parser.add_argument('--method')
    global args
    args = parser.parse_args()

    result = process_file(args.logfile, status_filter=args.status, method_filter=args.method)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        # Human readable
        print(f"Total requests: {result['total_requests']}")
        print(f"Total bytes: {result['total_bytes']}")
        print("Status counts:")
        for status, count in sorted(result['status_counts'].items()):
            print(f"  {status}: {count}")
        print("Top IPs:")
        for item in result['top_ips']:
            print(f"  {item['ip']}: {item['count']}")
        print("Top paths:")
        for item in result['top_paths']:
            print(f"  {item['path']}: {item['count']}")
        print(f"Malformed lines: {result['malformed_lines']}")

if __name__ == '__main__':
    main()
