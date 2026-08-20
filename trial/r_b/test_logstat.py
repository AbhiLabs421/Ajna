import unittest
import subprocess
import json
import os

# Helper to run logstat.py and capture output

def run_logstat(args, input_file=None):
    cmd = ['python', 'logstat.py'] + args
    if input_file:
        cmd.append(input_file)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

class TestLogStat(unittest.TestCase):
    def setUp(self):
        # Create a temporary log file
        self.log_path = 'temp_log.log'
        with open(self.log_path, 'w', encoding='utf-8') as f:
            f.write('127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n')
            f.write('127.0.0.1 - - [10/Oct/2023:13:56:00 +0000] "POST /submit HTTP/1.1" 404 -\n')
            f.write('invalid line that should be malformed\n')
            f.write('192.168.1.1 - - [10/Oct/2023:13:57:00 +0000] "GET /index.html HTTP/1.1" 200 1000\n')
            f.write('192.168.1.1 - - [10/Oct/2023:13:58:00 +0000] "GET /contact HTTP/1.1" 200 500\n')

    def tearDown(self):
        os.remove(self.log_path)

    def test_basic_counts(self):
        res = run_logstat(['--json', self.log_path])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 4)  # 1 malformed
        self.assertEqual(data['malformed_lines'], 1)
        self.assertEqual(data['total_bytes'], 2326+0+1000+500)
        self.assertEqual(data['status_counts']['200'], 3)
        self.assertEqual(data['status_counts']['404'], 1)
        # top_ips
        self.assertEqual(data['top_ips'][0]['ip'], '127.0.0.1')
        self.assertEqual(data['top_ips'][0]['count'], 2)
        self.assertEqual(data['top_ips'][1]['ip'], '192.168.1.1')
        self.assertEqual(data['top_ips'][1]['count'], 2)
        # tie-breaking by ip ascending
        self.assertLess(data['top_ips'][0]['ip'], data['top_ips'][1]['ip'])

    def test_filter_status(self):
        res = run_logstat(['--json', '--status', '200', self.log_path])
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 3)
        self.assertEqual(data['status_counts']['200'], 3)
        self.assertNotIn('404', data['status_counts'])

    def test_filter_method(self):
        res = run_logstat(['--json', '--method', 'GET', self.log_path])
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 3)
        self.assertEqual(data['status_counts']['200'], 2)
        self.assertEqual(data['status_counts']['404'], 1)

    def test_top_limit(self):
        res = run_logstat(['--json', '--top', '1', self.log_path])
        data = json.loads(res.stdout)
        self.assertEqual(len(data['top_ips']), 1)
        self.assertEqual(len(data['top_paths']), 1)

    def test_empty_file(self):
        empty_path = 'empty.log'
        open(empty_path, 'w').close()
        res = run_logstat(['--json', empty_path])
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 0)
        self.assertEqual(data['total_bytes'], 0)
        self.assertEqual(data['status_counts'], {})
        self.assertEqual(data['top_ips'], [])
        self.assertEqual(data['top_paths'], [])
        self.assertEqual(data['malformed_lines'], 0)
        os.remove(empty_path)

    def test_nonexistent_file(self):
        res = run_logstat(['nonexistent.log'])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('Error reading file', res.stderr)

if __name__ == '__main__':
    unittest.main()
