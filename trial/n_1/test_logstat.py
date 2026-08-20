import unittest
import subprocess
import json
import os

# Helper to run logstat with arguments and capture output

def run_logstat(args, input_file=None):
    cmd = ['python', 'logstat.py'] + args
    if input_file:
        cmd.append(input_file)
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result

class TestLogStat(unittest.TestCase):
    def setUp(self):
        # Create a sample log file
        self.log_path = 'sample.log'
        with open(self.log_path, 'w', encoding='utf-8') as f:
            f.write('127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n')
            f.write('127.0.0.1 - - [10/Oct/2023:13:56:01 +0000] "POST /submit HTTP/1.1" 404 512\n')
            f.write('192.168.1.5 - - [10/Oct/2023:13:57:12 +0000] "GET /index.html HTTP/1.1" 200 1024\n')
            f.write('bad line that is malformed\n')
            f.write('127.0.0.1 - - [10/Oct/2023:13:58:00 +0000] "GET /index.html HTTP/1.1" 200 -\n')

    def tearDown(self):
        if os.path.exists(self.log_path):
            os.remove(self.log_path)

    def test_json_output(self):
        result = run_logstat(['--json'], self.log_path)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 4)  # 4 well-formed lines
        self.assertEqual(data['malformed_lines'], 1)
        self.assertEqual(data['total_bytes'], 2326+512+1024+0)
        self.assertEqual(data['status_counts']['200'], 3)
        self.assertEqual(data['status_counts']['404'], 1)
        self.assertEqual(len(data['top_ips']), 2)
        self.assertEqual(data['top_ips'][0]['ip'], '127.0.0.1')
        self.assertEqual(data['top_ips'][0]['count'], 3)

    def test_filters(self):
        # Only status 200
        result = run_logstat(['--status', '200', '--json'], self.log_path)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 3)
        self.assertEqual(data['status_counts']['200'], 3)
        self.assertNotIn('404', data['status_counts'])

        # Only method GET
        result = run_logstat(['--method', 'GET', '--json'], self.log_path)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 3)

    def test_top_n_and_tie_breaking(self):
        # Add more entries to create ties
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write('10.0.0.1 - - [10/Oct/2023:13:59:00 +0000] "GET /index.html HTTP/1.1" 200 100\n')
            f.write('10.0.0.2 - - [10/Oct/2023:13:59:01 +0000] "GET /index.html HTTP/1.1" 200 100\n')
        result = run_logstat(['--top', '2', '--json'], self.log_path)
        data = json.loads(result.stdout)
        # top_ips should be sorted by count desc then ip asc
        self.assertEqual(data['top_ips'][0]['ip'], '127.0.0.1')
        self.assertEqual(data['top_ips'][0]['count'], 3)
        self.assertEqual(data['top_ips'][1]['ip'], '10.0.0.1')

    def test_empty_log(self):
        empty_path = 'empty.log'
        open(empty_path, 'w').close()
        result = run_logstat(['--json'], empty_path)
        data = json.loads(result.stdout)
        self.assertEqual(data['total_requests'], 0)
        self.assertEqual(data['total_bytes'], 0)
        self.assertEqual(data['status_counts'], {})
        self.assertEqual(data['top_ips'], [])
        self.assertEqual(data['top_paths'], [])
        self.assertEqual(data['malformed_lines'], 0)
        os.remove(empty_path)

    def test_nonexistent_file(self):
        result = run_logstat(['nonexistent.log'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Error opening file', result.stderr)

if __name__ == '__main__':
    unittest.main()
