#!/usr/bin/env python3
import sys
import argparse
import json
import re
from collections import Counter

CLF_RE = re.compile(r"^(\S+) (\S+) (\S+) \[([^\]]+)\] \"(\S+) (\S+) (\S+)\" (\d{3}) (\S+)$")


def parse_line(line):
    m = CLF_RE.match(line.strip())
    if not m:
        return None
    client_ip, identity, user, timestamp, method, path, protocol, status, size = m.groups()
    size = 0 if size == '-' else int(size)
    return {
        'client_ip': client_ip,
        'identity': identity,
        'user': user,
        'timestamp': timestamp,
        'method': method,
        'path': path,
        'protocol': protocol,
        'status': status,
        'size': size,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Summarise web server access log.')
    parser.add_argument('logfile', nargs='?', help='Path to log file (optional, reads stdin if omitted)')
    parser.add_argument('--json', action='store_true', help='Emit JSON output')
    parser.add_argument('--top', type=int, default=5, help='Number of top entries')
    parser.add_argument('--status', help='Filter by status code')
    parser.add_argument('--method', help='Filter by HTTP method (case-insensitive)')
    args = parser.parse_args(argv)

    if not args.logfile:
        f = sys.stdin
    else:
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
        if args.status and parsed['status'] != args.status:
            continue
        if args.method and parsed['method'].upper() != args.method.upper():
            continue
        total_requests += 1
        total_bytes += parsed['size']
        status_counts[parsed['status']] += 1
        ip_counts[parsed['client_ip']] += 1
        path_counts[parsed['path']] += 1

    f.close()

    top_n = args.top
    top_ips = sorted(ip_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    top_paths = sorted(path_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]

    if args.json:
        out = {
            'total_requests': total_requests,
            'total_bytes': total_bytes,
            'status_counts': {k: int(v) for k, v in status_counts.items()},
            'top_ips': [{'ip': ip, 'count': cnt} for ip, cnt in top_ips],
            'top_paths': [{'path': path, 'count': cnt} for path, cnt in top_paths],
            'malformed_lines': malformed_lines,
        }
        sys.stdout.write(json.dumps(out, indent=2))
        sys.stdout.write('\n')
    else:
        print(f"Total requests: {total_requests}")
        print(f"Total bytes: {total_bytes}")
        print("Status counts:")
        for status, cnt in sorted(status_counts.items()):
            print(f"  {status}: {cnt}")
        print("Top IPs:")
        for ip, cnt in top_ips:
            print(f"  {ip}: {cnt}")
        print("Top paths:")
        for path, cnt in top_paths:
            print(f"  {path}: {cnt}")
        print(f"Malformed lines: {malformed_lines}")


if __name__ == '__main__':
    main()
