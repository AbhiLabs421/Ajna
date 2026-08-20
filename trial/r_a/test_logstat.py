import unittest
import subprocess
import json
import os

class TestLogstat(unittest.TestCase):
    def run_logstat(self, args, input_data=None):
        cmd = ['python', 'logstat.py'] + args
        result = subprocess.run(cmd, input=input_data, text=True, capture_output=True)
        return result

    def test_basic(self):
        log = (
            '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n'
            '127.0.0.1 - - [10/Oct/2023:13:56:00 +0000] "POST /submit HTTP/1.1" 404 512\n'
            '127.0.0.1 - - [10/Oct/2023:13:57:00 +0000] "GET /index.html HTTP/1.1" 200 -\n'
        )
        result = self.run_logstat(['--json'], input_data=log)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 3)
        self.assertEqual(data['total_bytes'], 2326+512+0)
        self.assertEqual(data['status_counts'], {'200':2,'404':1})
        self.assertEqual(data['malformed_lines'],0)

    def test_filters(self):
        log = (
            '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n'
            '127.0.0.1 - - [10/Oct/2023:13:56:00 +0000] "POST /submit HTTP/1.1" 404 512\n'
            '127.0.0.1 - - [10/Oct/2023:13:57:00 +0000] "GET /index.html HTTP/1.1" 200 -\n'
        )
        result = self.run_logstat(['--status','200','--json'], input_data=log)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'],2)
        self.assertEqual(data['status_counts'],{'200':2})

    def test_top_and_tie_breaking(self):
        log = ''
        for i in range(3):
            log += f'1.1.1.{i} - - [10/Oct/2023:13:55:36 +0000] "GET /a HTTP/1.1" 200 100\n'
        for i in range(3):
            log += f'2.2.2.{i} - - [10/Oct/2023:13:55:36 +0000] "GET /b HTTP/1.1" 200 100\n'
        result = self.run_logstat(['--top','2','--json'], input_data=log)
        data = json.loads(result.stdout)
        # top_ips should be sorted by count desc, then ip asc
        self.assertEqual([e['ip'] for e in data['top_ips']], ['1.1.1.0','1.1.1.1'])
        self.assertEqual([e['ip'] for e in data['top_paths']], ['/a','/b'])

    def test_malformed(self):
        log = 'bad line\n127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n'
        result = self.run_logstat(['--json'], input_data=log)
        data = json.loads(result.stdout)
        self.assertEqual(data['malformed_lines'],1)
        self.assertEqual(data['total_requests'],1)

    def test_file_not_found(self):
        result = self.run_logstat(['nonexistent.log'])
        self.assertEqual(result.returncode,2)
        self.assertIn("cannot read file", result.stderr)

if __name__ == '__main__':
    unittest.main()
