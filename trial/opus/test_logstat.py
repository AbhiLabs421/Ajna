import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logstat

GOOD = ('10.0.0.1 - - [10/Oct/2023:13:55:36 +0000] '
        '"GET /index.html HTTP/1.1" 200 2326')


class ParseTest(unittest.TestCase):
    def test_full_line(self):
        entry = logstat.parse_line(GOOD)
        self.assertEqual(entry.ip, "10.0.0.1")
        self.assertEqual(entry.method, "GET")
        self.assertEqual(entry.path, "/index.html")
        self.assertEqual(entry.status, "200")
        self.assertEqual(entry.size, 2326)

    def test_dash_size_is_zero(self):
        entry = logstat.parse_line(GOOD.replace("2326", "-"))
        self.assertEqual(entry.size, 0)

    def test_status_stays_a_string(self):
        self.assertIsInstance(logstat.parse_line(GOOD).status, str)

    def test_path_with_query_string(self):
        line = GOOD.replace("/index.html", "/search?q=a+b&page=2")
        self.assertEqual(logstat.parse_line(line).path, "/search?q=a+b&page=2")

    def test_quotes_inside_the_user_agent_do_not_confuse_it(self):
        entry = logstat.parse_line(GOOD)
        self.assertIsNotNone(entry)

    def test_rejects_junk(self):
        for bad in ("", "   ", "not a log line",
                    '10.0.0.1 - - [bad] "GET / HTTP/1.1" 200',
                    '10.0.0.1 - - [t] "GET / HTTP/1.1" 20 100',
                    '10.0.0.1 - - [t] "" 200 100'):
            self.assertIsNone(logstat.parse_line(bad), bad)

    def test_rejects_a_two_token_request(self):
        self.assertIsNone(logstat.parse_line(
            '10.0.0.1 - - [t] "GET /only-two" 200 5'))

    def test_trailing_junk_is_rejected(self):
        self.assertIsNone(logstat.parse_line(GOOD + " extra"))


class ReadTest(unittest.TestCase):
    def test_counts_malformed_and_skips_blanks(self):
        text = GOOD + "\n\nbroken\n\n" + GOOD + "\nalso broken\n"
        entries, malformed = logstat.read_log(io.StringIO(text))
        self.assertEqual(len(entries), 2)
        self.assertEqual(malformed, 2)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            logstat.Entry("a", "GET", "/x", "200", 10),
            logstat.Entry("b", "POST", "/y", "404", 20),
            logstat.Entry("c", "get", "/z", "200", 30),
        ]

    def test_status(self):
        kept = [e for e in self.entries if logstat.keep(e, status="200")]
        self.assertEqual(len(kept), 2)

    def test_method_is_case_insensitive(self):
        kept = [e for e in self.entries if logstat.keep(e, method="get")]
        self.assertEqual(len(kept), 2)

    def test_filters_are_an_and(self):
        kept = [e for e in self.entries
                if logstat.keep(e, status="200", method="POST")]
        self.assertEqual(kept, [])

    def test_no_filter_keeps_everything(self):
        kept = [e for e in self.entries if logstat.keep(e)]
        self.assertEqual(len(kept), 3)


class SummaryTest(unittest.TestCase):
    def test_ties_break_on_name_ascending(self):
        entries = [logstat.Entry(ip, "GET", "/" + ip, "200", 1)
                   for ip in ("b", "a", "c", "a", "b")]
        summary = logstat.summarise(entries, 0)
        self.assertEqual([row["ip"] for row in summary["top_ips"]],
                         ["a", "b", "c"])

    def test_top_limits_lists_but_not_totals(self):
        entries = [logstat.Entry("ip%d" % i, "GET", "/p%d" % i, "200", 1)
                   for i in range(10)]
        summary = logstat.summarise(entries, 0, top=3)
        self.assertEqual(len(summary["top_ips"]), 3)
        self.assertEqual(summary["total_requests"], 10)

    def test_empty_log_has_every_key(self):
        summary = logstat.summarise([], 0)
        self.assertEqual(summary, {
            "total_requests": 0, "total_bytes": 0, "status_counts": {},
            "top_ips": [], "top_paths": [], "malformed_lines": 0,
        })

    def test_status_keys_are_strings(self):
        summary = logstat.summarise([logstat.Entry("a", "GET", "/", "404", 1)], 0)
        self.assertEqual(summary["status_counts"], {"404": 1})


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "a.log")
        with open(self.log, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(GOOD + "\njunk\n")

    def _run(self, *args):
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "logstat.py")] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"))

    def test_json_round_trip(self):
        code, out, _ = self._run(self.log, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total_requests"], 1)
        self.assertEqual(data["malformed_lines"], 1)

    def test_missing_file_exits_two_and_says_nothing_on_stdout(self):
        code, out, err = self._run(os.path.join(self.dir, "nope.log"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("nope.log", err)

    def test_readable_report_is_not_json(self):
        code, out, _ = self._run(self.log)
        self.assertEqual(code, 0)
        self.assertFalse(out.strip().startswith("{"))
        self.assertIn("10.0.0.1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
