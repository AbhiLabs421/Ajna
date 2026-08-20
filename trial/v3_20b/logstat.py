#!/usr/bin/env python3
import sys
import argparse
import re
import json

CLF_RE = re.compile(r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+\"(?P<method>\S+)\s+(?P<path>\S+)\s+\S+\"\s+(?P<status>\d{3})\s+(?P<size>\S+)$")

def parse_line(line):
    m = CLF_RE.match(line.strip())
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
    parser.add_argument('logfile')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--top', type=int, default=5)
    parser.add_argument('--status')
    parser.add_argument('--method')
    args = parser.parse_args(argv)

    try:
        f = open(args.logfile, 'r', encoding='utf-8')
    except Exception as e:
        sys.stderr.write(f"Error opening file {args.logfile}: {e}\n")
        sys.exit(2)

    total_requests = 0
    total_bytes = 0
    status_counts = {}
    ip_counts = {}
    path_counts = {}
    malformed_lines = 0

    for line in f:
        parsed = parse_line(line)
        if not parsed:
            malformed_lines += 1
            continue
        # apply filters
        if args.status and parsed['status'] != args.status:
            continue
        if args.method and parsed['method'].lower() != args.method.lower():
            continue
        total_requests += 1
        total_bytes += parsed['size']
        status_counts[parsed['status']] = status_counts.get(parsed['status'], 0) + 1
        ip_counts[parsed['ip']] = ip_counts.get(parsed['ip'], 0) + 1
        path_counts[parsed['path']] = path_counts.get(parsed['path'], 0) + 1

    # top lists
    def top_counts(counts):
        return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:args.top]

    top_ips = [{'ip': ip, 'count': cnt} for ip, cnt in top_counts(ip_counts)]
    top_paths = [{'path': path, 'count': cnt} for path, cnt in top_counts(path_counts)]

    if args.json:
        obj = {
            'total_requests': total_requests,
            'total_bytes': total_bytes,
            'status_counts': {k: v for k, v in status_counts.items()},
            'top_ips': top_ips,
            'top_paths': top_paths,
            'malformed_lines': malformed_lines,
        }
        sys.stdout.write(json.dumps(obj, separators=(',', ':')) + '\n')
    else:
        print(f'Total requests: {total_requests}')
        print(f'Total bytes: {total_bytes}')
        print('Status distribution:')
        for status, cnt in sorted(status_counts.items()):
            print(f'  {status}: {cnt}')
        print('Top IPs:')
        for entry in top_ips:
            print(f'  {entry["ip"]}: {entry["count"]}')
        print('Top paths:')
        for entry in top_paths:
            print(f'  {entry["path"]}: {entry["count"]}')
        print(f'Malformed lines: {malformed_lines}')

if __name__ == '__main__':
    main()
