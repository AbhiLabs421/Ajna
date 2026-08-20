import unittest, subprocess, tempfile, os, json

class TestLogstat(unittest.TestCase):
    def setUp(self):
        # Create a temporary log file with known content
        self.temp = tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8')
        self.log_path = self.temp.name
        lines = [
            '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326\n',
            '127.0.0.1 - - [10/Oct/2023:13:56:01 +0000] "POST /submit HTTP/1.1" 404 0\n',
            '192.168.1.1 - - [10/Oct/2023:13:57:12 +0000] "GET /index.html HTTP/1.1" 200 2326\n',
            'bad line that does not match\n',
            '127.0.0.1 - - [10/Oct/2023:13:58:00 +0000] "GET /index.html HTTP/1.1" 200 -\n',
        ]
        self.temp.writelines(lines)
        self.temp.close()

    def tearDown(self):
        os.unlink(self.log_path)

    def run_logstat(self, args):
        cmd = ['python', 'logstat.py', self.log_path] + args
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result

    def test_json_output(self):
        res = self.run_logstat(['--json'])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 4)  # 4 well-formed lines, 1 malformed
        self.assertEqual(data['malformed_lines'], 1)
        self.assertEqual(data['total_bytes'], 2326+0+2326+0)
        self.assertEqual(data['status_counts'], {'200': 3, '404': 1})
        self.assertEqual(data['top_ips'], [{'ip': '127.0.0.1', 'count': 3}, {'ip': '192.168.1.1', 'count': 1}])
        self.assertEqual(data['top_paths'], [{'path': '/index.html', 'count': 3}, {'path': '/submit', 'count': 1}])

    def test_status_filter(self):
        res = self.run_logstat(['--json', '--status', '404'])
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 1)
        self.assertEqual(data['status_counts'], {'404': 1})

    def test_method_filter(self):
        res = self.run_logstat(['--json', '--method', 'post'])
        data = json.loads(res.stdout)
        self.assertEqual(data['total_requests'], 1)
        self.assertEqual(data['status_counts'], {'404': 1})

    def test_tie_breaking(self):
        # Add more lines to create tie for IP counts
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write('10.0.0.1 - - [10/Oct/2023:13:59:00 +0000] "GET /home HTTP/1.1" 200 100\n')
            f.write('10.0.0.2 - - [10/Oct/2023:14:00:00 +0000] "GET /home HTTP/1.1" 200 100\n')
        res = self.run_logstat(['--json', '--top', '3'])
        data = json.loads(res.stdout)
        # 10.0.0.1 and 10.0.0.2 each have 1, 127.0.0.1 has 3
        expected_ips = [
            {'ip': '127.0.0.1', 'count': 3},
            {'ip': '10.0.0.1', 'count': 1},
            {'ip': '10.0.0.2', 'count': 1},
        ]
        self.assertEqual(data['top_ips'], expected_ips)

    def test_missing_file(self):
        res = subprocess.run(['python', 'logstat.py', 'nonexistent.log'], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("cannot read file", res.stderr)

if __name__ == '__main__':
    unittest.main()
