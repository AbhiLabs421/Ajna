import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

# Import parse_line directly for unit tests
import importlib.util
spec = importlib.util.spec_from_file_location('logstat', Path('logstat.py'))
logstat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logstat)

class TestLogstat(unittest.TestCase):
    def test_parse_line_valid(self):
        line = '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326'
        parsed = logstat.parse_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['ip'], '127.0.0.1')
        self.assertEqual(parsed['method'], 'GET')
        self.assertEqual(parsed['path'], '/index.html')
        self.assertEqual(parsed['status'], '200')
        self.assertEqual(parsed['size'], 2326)

    def test_parse_line_size_dash(self):
        line = '1.2.3.4 - - [01/Jan/2024:00:00:00 +0000] "POST /api HTTP/1.1" 201 -'
        parsed = logstat.parse_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['size'], 0)

    def test_parse_line_malformed(self):
        malformed = 'this is not a log line'
        self.assertIsNone(logstat.parse_line(malformed))

    def run_logstat(self, args, input_path):
        cmd = [sys.executable, 'logstat.py'] + args + [str(input_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result

    def test_json_output(self):
        log_content = """
127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /a HTTP/1.1" 200 100
127.0.0.2 - - [10/Oct/2023:13:55:37 +0000] "GET /b HTTP/1.1" 404 200
malformed line here
127.0.0.1 - - [10/Oct/2023:13:55:38 +0000] "POST /a HTTP/1.1" 200 150
"""
        tmp = Path('tmp.log')
        tmp.write_text(log_content.strip())
        result = self.run_logstat(['--json'], tmp)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 3)
        self.assertEqual(data['total_bytes'], 450)
        self.assertEqual(data['status_counts'], {'200': 2, '404': 1})
        self.assertEqual(data['malformed_lines'], 1)
        # top_ips should have 127.0.0.1 count 2, 127.0.0.2 count 1
        self.assertEqual(data['top_ips'][0]['ip'], '127.0.0.1')
        self.assertEqual(data['top_ips'][0]['count'], 2)
        # top_paths: /a count 2, /b count 1
        self.assertEqual(data['top_paths'][0]['path'], '/a')
        self.assertEqual(data['top_paths'][0]['count'], 2)
        tmp.unlink()

    def test_filters(self):
        log_content = """
1.1.1.1 - - [10/Oct/2023:13:55:36 +0000] "GET /x HTTP/1.1" 200 100
2.2.2.2 - - [10/Oct/2023:13:55:37 +0000] "POST /y HTTP/1.1" 200 200
3.3.3.3 - - [10/Oct/2023:13:55:38 +0000] "GET /z HTTP/1.1" 404 300
"""
        tmp = Path('tmp2.log')
        tmp.write_text(log_content.strip())
        # Filter by status 200
        result = self.run_logstat(['--json', '--status', '200'], tmp)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 2)
        self.assertEqual(data['total_bytes'], 300)
        # Filter by method GET (case-insensitive)
        result = self.run_logstat(['--json', '--method', 'get'], tmp)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 2)
        self.assertEqual(data['total_bytes'], 400)
        tmp.unlink()

    def test_tie_breaking(self):
        # Two IPs with same count, ensure alphabetical order
        log_content = """
b.ip - - [10/Oct/2023:13:55:36 +0000] "GET /a HTTP/1.1" 200 10
a.ip - - [10/Oct/2023:13:55:37 +0000] "GET /b HTTP/1.1" 200 20
b.ip - - [10/Oct/2023:13:55:38 +0000] "GET /c HTTP/1.1" 200 30
a.ip - - [10/Oct/2023:13:55:39 +0000] "GET /d HTTP/1.1" 200 40
"""
        tmp = Path('tmp3.log')
        tmp.write_text(log_content.strip())
        result = self.run_logstat(['--json', '--top', '2'], tmp)
        data = json.loads(result.stdout)
        # Both a.ip and b.ip have count 2; a.ip should come first alphabetically
        self.assertEqual(data['top_ips'][0]['ip'], 'a.ip')
        self.assertEqual(data['top_ips'][1]['ip'], 'b.ip')
        # Paths also have same counts; alphabetical order
        self.assertEqual(data['top_paths'][0]['path'], '/a')
        self.assertEqual(data['top_paths'][1]['path'], '/b')
        tmp.unlink()

if __name__ == '__main__':
    unittest.main()
