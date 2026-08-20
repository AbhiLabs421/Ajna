#!/usr/bin/env python3
import argparse, json, sys, re

LOG_PATTERN = re.compile(r"^(\S+) (\S+) (\S+) \[([^\]]+)\] \"([^\"]+)\" (\d{3}) (\S+)$")


def parse_line(line):
    m = LOG_PATTERN.match(line.rstrip("\n"))
    if not m:
        return None
    ip, ident, user, ts, request, status, size = m.groups()
    try:
        method, path, protocol = request.split(" ")
    except ValueError:
        return None
    size_val = 0 if size == "-" else int(size)
    return {
        "ip": ip,
        "ident": ident,
        "user": user,
        "timestamp": ts,
        "method": method,
        "path": path,
        "protocol": protocol,
        "status": status,
        "size": size_val,
    }


def process_file(path, status_filter=None, method_filter=None):
    total_requests = 0
    total_bytes = 0
    status_counts = {}
    ip_counts = {}
    path_counts = {}
    malformed_lines = 0
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"Error opening file {path}: {e}", file=sys.stderr)
        sys.exit(2)
    for line in f:
        rec = parse_line(line)
        if rec is None:
            malformed_lines += 1
            continue
        if status_filter and rec["status"] != status_filter:
            continue
        if method_filter and rec["method"].lower() != method_filter.lower():
            continue
        total_requests += 1
        total_bytes += rec["size"]
        status_counts[rec["status"]] = status_counts.get(rec["status"], 0) + 1
        ip_counts[rec["ip"]] = ip_counts.get(rec["ip"], 0) + 1
        path_counts[rec["path"]] = path_counts.get(rec["path"], 0) + 1
    f.close()
    return {
        "total_requests": total_requests,
        "total_bytes": total_bytes,
        "status_counts": status_counts,
        "ip_counts": ip_counts,
        "path_counts": path_counts,
        "malformed_lines": malformed_lines,
    }


def top_n(counts, top_n):
    items = list(counts.items())
    items.sort(key=lambda x: (-x[1], x[0]))
    return [{"ip" if k == "ip_counts" else "path": k, "count": v} for k, v in items[:top_n]]


def main():
    parser = argparse.ArgumentParser(description="Summarise web server access log")
    parser.add_argument("logfile")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--status")
    parser.add_argument("--method")
    args = parser.parse_args()

    result = process_file(args.logfile, args.status, args.method)

    if args.json:
        output = {
            "total_requests": result["total_requests"],
            "total_bytes": result["total_bytes"],
            "status_counts": result["status_counts"],
            "top_ips": sorted(
                [{"ip": k, "count": v} for k, v in result["ip_counts"].items()],
                key=lambda x: (-x["count"], x["ip"]),
            )[:args.top],
            "top_paths": sorted(
                [{"path": k, "count": v} for k, v in result["path_counts"].items()],
                key=lambda x: (-x["count"], x["path"]),
            )[:args.top],
            "malformed_lines": result["malformed_lines"],
        }
        print(json.dumps(output, ensure_ascii=False, separators=(",", ": ")))
    else:
        print(f"Total requests: {result['total_requests']}")
        print(f"Total bytes: {result['total_bytes']}")
        print("Status counts:")
        for status, count in sorted(result["status_counts"].items()):
            print(f"  {status}: {count}")
        print("Top IPs:")
        for item in sorted(
            [{"ip": k, "count": v} for k, v in result["ip_counts"].items()],
            key=lambda x: (-x["count"], x["ip"]),
        )[:args.top]:
            print(f"  {item['ip']}: {item['count']}")
        print("Top paths:")
        for item in sorted(
            [{"path": k, "count": v} for k, v in result["path_counts"].items()],
            key=lambda x: (-x["count"], x["path"]),
        )[:args.top]:
            print(f"  {item['path']}: {item['count']}")
        print(f"Malformed lines: {result['malformed_lines']}")


if __name__ == "__main__":
    main()
