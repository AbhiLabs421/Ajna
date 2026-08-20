#!/usr/bin/env python3
import argparse
import json
import sys
import re
from collections import Counter

LOG_PATTERN = re.compile(r"(?P<ip>\S+) (?P<ident>\S+) (?P<user>\S+) \[(?P<time>[^]]+)\] \"(?P<method>\S+) (?P<path>\S+) (?P<protocol>[^\"]+)\" (?P<status>\d{3}) (?P<size>\S+)")

def parse_line(line: str):
    m = LOG_PATTERN.match(line)
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

def process_log(file_path, status=None, method=None, top_n=5):
    total_requests = 0
    total_bytes = 0
    status_counts = Counter()
    ip_counts = Counter()
    path_counts = Counter()
    malformed_lines = 0
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                if not line.strip():
            continue
        parsed = parse_line(line)
                if not parsed:
                    malformed_lines += 1
                    continue
                if status and parsed['status'] != status:
                    continue
                if method and parsed['method'].lower() != method.lower():
                    continue
                total_requests += 1
                total_bytes += parsed['size']
                status_counts[parsed['status']] += 1
                ip_counts[parsed['ip']] += 1
                path_counts[parsed['path']] += 1
    except FileNotFoundError:
        print(f"Error: file '{file_path}' not found", file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"Error: cannot read file '{file_path}': {e}", file=sys.stderr)
        sys.exit(2)
    # Prepare top lists
    def top_list(counter):
        items = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
        return [{'ip' if key in ip_counts else 'path': key, 'count': count} for key, count in items[:top_n]]
    top_ips = top_list(ip_counts)
    top_paths = top_list(path_counts)
    return {
        'total_requests': total_requests,
        'total_bytes': total_bytes,
        'status_counts': dict(status_counts),
        'top_ips': top_ips,
        'top_paths': top_paths,
        'malformed_lines': malformed_lines,
    }

def main():
    parser = argparse.ArgumentParser(description='Summarise web server access log')
    parser.add_argument('logfile')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--top', type=int, default=5)
    parser.add_argument('--status')
    parser.add_argument('--method')
    args = parser.parse_args()
    result = process_log(args.logfile, status=args.status, method=args.method, top_n=args.top)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ": ")))
    else:
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
