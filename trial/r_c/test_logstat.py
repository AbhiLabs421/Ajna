import unittest, tempfile, os, json
from logstat import parse_line, process_log

class TestLogStat(unittest.TestCase):
    def setUp(self):
        self.log_content = """
127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] \"GET /index.html HTTP/1.1\" 200 2326
127.0.0.1 - - [10/Oct/2023:13:56:01 +0000] \"POST /submit HTTP/1.1\" 404 0
192.168.1.1 - - [10/Oct/2023:13:57:12 +0000] \"GET /index.html HTTP/1.1\" 200 1024
invalid line that should be malformed
127.0.0.1 - - [10/Oct/2023:13:58:00 +0000] \"GET /index.html HTTP/1.1\" 200 -
"""
        self.tmp = tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8')
        self.tmp.write(self.log_content)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        os.unlink(self.path)

    def test_parsing(self):
        line = '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326'
        parsed = parse_line(line)
        self.assertEqual(parsed['ip'], '127.0.0.1')
        self.assertEqual(parsed['method'], 'GET')
        self.assertEqual(parsed['path'], '/index.html')
        self.assertEqual(parsed['status'], '200')
        self.assertEqual(parsed['size'], 2326)

    def test_malformed(self):
        result = process_log(self.path)
        self.assertEqual(result['malformed_lines'], 1)

    def test_filters(self):
        result = process_log(self.path, status='200')
        self.assertEqual(result['total_requests'], 3)
        result = process_log(self.path, method='GET')
        self.assertEqual(result['total_requests'], 3)
        result = process_log(self.path, status='404', method='POST')
        self.assertEqual(result['total_requests'], 1)

    def test_top_tie_breaking(self):
        # Add more lines to create tie
        extra = """
127.0.0.1 - - [10/Oct/2023:13:59:00 +0000] \"GET /home HTTP/1.1\" 200 500
192.168.1.1 - - [10/Oct/2023:14:00:00 +0000] \"GET /home HTTP/1.1\" 200 500
"""
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(extra)
        result = process_log(self.path, top_n=2)
        # top_ips should be sorted by count desc then ip asc
        self.assertEqual(result['top_ips'][0]['ip'], '127.0.0.1')
        self.assertEqual(result['top_ips'][1]['ip'], '192.168.1.1')
        # top_paths should be sorted by count desc then path asc
        self.assertEqual(result['top_paths'][0]['path'], '/index.html')
        self.assertEqual(result['top_paths'][1]['path'], '/home')

if __name__ == '__main__':
    unittest.main()
