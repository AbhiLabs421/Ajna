# logstat — build this

Write a single-file command line tool, `logstat.py`, that summarises a web
server access log. Python 3 standard library only. No third-party packages.

## Input

Lines in Common Log Format:

```
127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
```

Fields, in order: client IP, identity, user, `[timestamp]`, `"method path
protocol"`, status code, response size in bytes.

A size of `-` means zero bytes. Any line that does not match the format is
*malformed*: it must be counted, and must not crash the tool or corrupt the
other numbers.

## Usage

```
python logstat.py <logfile> [--json] [--top N] [--status CODE] [--method M]
```

- `<logfile>` — path to the log. Required.
- `--json` — emit the JSON object below instead of the human-readable report.
- `--top N` — how many entries in `top_ips` and `top_paths`. Default 5.
- `--status CODE` — consider only requests with this status code.
- `--method M` — consider only requests with this HTTP method, case-insensitive.

Filters apply before anything is counted. Filters combine with AND.

## JSON output

`--json` must print exactly this shape and nothing else:

```json
{
  "total_requests": 12,
  "total_bytes": 45678,
  "status_counts": {"200": 8, "404": 3, "500": 1},
  "top_ips": [{"ip": "1.2.3.4", "count": 5}],
  "top_paths": [{"path": "/index.html", "count": 4}],
  "malformed_lines": 2
}
```

Rules:

- `total_requests` counts only well-formed lines that survive the filters.
- `total_bytes` sums the size field over those same requests.
- `status_counts` keys are strings; only statuses actually present appear.
- `top_ips` and `top_paths` are sorted by `count` descending. Ties are broken
  by the ip or path ascending, so the output is deterministic.
- `malformed_lines` counts unparseable lines in the whole file. Filters do not
  apply to it.
- Every key is present even when the log is empty, with `0`, `{}` or `[]`.

## Human-readable output

Without `--json`, print a readable report. The exact layout is yours, but it
must show the total request count, total bytes, the status distribution, the
top IPs and the top paths.

## Exit codes and errors

- `0` — success.
- `2` — the log file does not exist or cannot be read. Print a clear message
  to **stderr**, print nothing to stdout.

An empty log file is not an error: it exits `0` with all counters at zero.

## Tests

Ship `test_logstat.py` covering the parsing, the filters, the tie-breaking and
the malformed-line handling. `python -m unittest` must pass.
