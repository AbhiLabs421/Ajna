#!/usr/bin/env python3
import sys
import argparse
import re
import json

# Regular expression for Common Log Format
LOG_RE = re.compile(r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+\"(?P<method>\S+)\s+(?P<path>\S+)\s+[^\"]+\"\s+(?P<status>\d{3})\s+(?P<size>\S+)$")


def parse_line(line):
    m = LOG_RE.match(line.strip())
    if not m:
        return None
    size = m.group('size')
    size = 0 if size == '-' else int(size)
    return {
        'ip': m.group('ip'),
        'method': m.group('method'),
        'path': m.group('path'),
        'status': m.group('status'),
        'size': size,
    }


def compute_stats(lines, status_filter=None, method_filter=None, top_n=5):
    total_requests = 0
    total_bytes = 0
    status_counts = {}
    ip_counts = {}
    path_counts = {}
    malformed_lines = 0

    for line in lines:
        parsed = parse_line(line)
        if not parsed:
            malformed_lines += 1
            continue
        # Apply filters
        if status_filter and parsed['status'] != status_filter:
            continue
        if method_filter and parsed['method'].lower() != method_filter.lower():
            continue
        total_requests += 1
        total_bytes += parsed['size']
        status_counts[parsed['status']] = status_counts.get(parsed['status'], 0) + 1
        ip_counts[parsed['ip']] = ip_counts.get(parsed['ip'], 0) + 1
        path_counts[parsed['path']] = path_counts.get(parsed['path'], 0) + 1

    def top_items(counts, key_name):
        items = [{key_name: k, 'count': v} for k, v in counts.items()]
        if key_name == 'ip':
            items.sort(key=lambda x: (-x['count'], -int(''.join(part.zfill(3) for part in x[key_name].split('.')))))
        else:
            items.sort(key=lambda x: (-x['count'], x[key_name]))
        return items[:top_n]

    top_ips = top_items(ip_counts, 'ip')
    top_paths = top_items(path_counts, 'path')

    return {
        'total_requests': total_requests,
        'total_bytes': total_bytes,
        'status_counts': status_counts,
        'top_ips': top_ips,
        'top_paths': top_paths,
        'malformed_lines': malformed_lines,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Summarise web server access log')
    parser.add_argument('logfile')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--top', type=int, default=5)
    parser.add_argument('--status')
    parser.add_argument('--method')
    args = parser.parse_args(argv)

    try:
        with open(args.logfile, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        sys.stderr.write(f"Error reading file {args.logfile}: {e}\n")
        sys.exit(2)

    stats = compute_stats(lines, status_filter=args.status, method_filter=args.method, top_n=args.top)

    if args.json:
        # Ensure status_counts keys are strings
        stats['status_counts'] = {k: v for k, v in stats['status_counts'].items()}
        print(json.dumps(stats, indent=2))
    else:
        # Human readable
        print(f"Total requests: {stats['total_requests']}")
        print(f"Total bytes: {stats['total_bytes']}")
        print("Status counts:")
        for status, count in sorted(stats['status_counts'].items()):
            print(f"  {status}: {count}")
        print("Top IPs:")
        for item in stats['top_ips']:
            print(f"  {item['ip']}: {item['count']}")
        print("Top paths:")
        for item in stats['top_paths']:
            print(f"  {item['path']}: {item['count']}")
        print(f"Malformed lines: {stats['malformed_lines']}")

if __name__ == '__main__':
    main()
