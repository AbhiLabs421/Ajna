import unittest

from logstat import parse_line, LOG_RE

class TestLogStat(unittest.TestCase):
    def test_parse_valid(self):
        line = '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326'
        d = parse_line(line)
        self.assertEqual(d['ip'], '127.0.0.1')
        self.assertEqual(d['method'], 'GET')
        self.assertEqual(d['path'], '/index.html')
        self.assertEqual(d['status'], '200')
        self.assertEqual(d['size'], 2326)

    def test_parse_size_dash(self):
        line = '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 -'
        d = parse_line(line)
        self.assertEqual(d['size'], 0)

    def test_malformed(self):
        line = 'this is not a log line'
        with self.assertRaises(ValueError):
            parse_line(line)

    def test_regex_match(self):
        # ensure LOG_RE matches the same pattern as parse_line
        line = '127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326'
        m = LOG_RE.match(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group('ip'), '127.0.0.1')
        self.assertEqual(m.group('method'), 'GET')
        self.assertEqual(m.group('path'), '/index.html')
        self.assertEqual(m.group('status'), '200')
        self.assertEqual(m.group('size'), '2326')

if __name__ == '__main__':
    unittest.main()
