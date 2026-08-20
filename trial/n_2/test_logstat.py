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
        self.logfile = os.path.join(os.path.dirname(__file__), 'temp.log')
        with open(self.logfile, 'w', encoding='utf-8') as f:
            f.write(
                '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n'
                '127.0.0.1 - - [10/Oct/2023:13:56:01 +0000] "POST /submit HTTP/1.1" 404 0\n'
                '1.2.3.4 - - [10/Oct/2023:13:57:12 +0000] "GET /index.html HTTP/1.1" 200 1000\n'
                '1.2.3.4 - - [10/Oct/2023:13:58:45 +0000] "GET /about HTTP/1.1" 200 -\n'
                'bad line that is malformed\n'
            )

    def tearDown(self):
        os.remove(self.logfile)

    def test_json_output(self):
        res = run_logstat(['--json'], self.logfile)
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 4)
        self.assertEqual(data['malformed_lines'], 1)
        self.assertEqual(data['total_bytes'], 2326+0+1000+0)
        self.assertEqual(data['status_counts']['200'], 3)
        self.assertEqual(data['status_counts']['404'], 1)
        self.assertEqual(len(data['top_ips']), 2)
        self.assertEqual(data['top_ips'][0]['ip'], '127.0.0.1')
        self.assertEqual(data['top_ips'][0]['count'], 2)

    def test_filters(self):
        res = run_logstat(['--status', '200', '--method', 'GET', '--json'], self.logfile)
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 3)  # 200 GET lines
        self.assertEqual(data['status_counts']['200'], 3)

    def test_top_tie_breaking(self):
        # Add more lines to create tie
        with open(self.logfile, 'a', encoding='utf-8') as f:
            f.write('1.2.3.4 - - [10/Oct/2023:13:59:00 +0000] "GET /index.html HTTP/1.1" 200 0\n')
        res = run_logstat(['--top', '1', '--json'], self.logfile)
        data = json.loads(res.stdout)
        # Now 127.0.0.1 and 1.2.3.4 both have 3 counts, tie broken by ip ascending
        self.assertEqual(data['top_ips'][0]['ip'], '127.0.0.1')

    def test_malformed_count(self):
        res = run_logstat(['--json'], self.logfile)
        data = json.loads(res.stdout)
        self.assertEqual(data['malformed_lines'], 1)

    def test_missing_file(self):
        res = run_logstat(['nonexistent.log'])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('Error reading file', res.stderr)

if __name__ == '__main__':
    unittest.main()
