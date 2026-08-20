import unittest, os, subprocess, json, textwrap

class TestLogstat(unittest.TestCase):
    def run_logstat(self, args, input_data=None):
        cmd = ['python', 'logstat.py'] + args
        result = subprocess.run(cmd, input=input_data, text=True, capture_output=True)
        return result

    def test_empty_file(self):
        with open('empty.log', 'w') as f:
            pass
        r = self.run_logstat(['empty.log', '--json'])
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out['total_requests'], 0)
        self.assertEqual(out['total_bytes'], 0)
        self.assertEqual(out['status_counts'], {})
        self.assertEqual(out['top_ips'], [])
        self.assertEqual(out['top_paths'], [])
        self.assertEqual(out['malformed_lines'], 0)

    def test_malformed_lines(self):
        data = textwrap.dedent('''
            127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
            malformed line
            127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /about.html HTTP/1.1" 404 -
        ''')
        with open('sample.log', 'w') as f:
            f.write(data)
        r = self.run_logstat(['sample.log', '--json'])
        out = json.loads(r.stdout)
        self.assertEqual(out['total_requests'], 2)
        self.assertEqual(out['malformed_lines'], 1)
        self.assertEqual(out['total_bytes'], 2326)
        self.assertEqual(out['status_counts'], {'200':1,'404':1})

    def test_filters(self):
        data = textwrap.dedent('''
            1.1.1.1 - - [10/Oct/2023:13:55:36 +0000] "GET /a HTTP/1.1" 200 100
            2.2.2.2 - - [10/Oct/2023:13:55:36 +0000] "POST /b HTTP/1.1" 200 200
            3.3.3.3 - - [10/Oct/2023:13:55:36 +0000] "GET /c HTTP/1.1" 404 300
        ''')
        with open('filter.log', 'w') as f:
            f.write(data)
        r = self.run_logstat(['filter.log', '--status', '200', '--method', 'GET', '--json'])
        out = json.loads(r.stdout)
        self.assertEqual(out['total_requests'], 1)
        self.assertEqual(out['total_bytes'], 100)
        self.assertEqual(out['status_counts'], {'200':1})
        self.assertEqual(out['top_ips'], [{'ip':'1.1.1.1','count':1}])
        self.assertEqual(out['top_paths'], [{'path':'/a','count':1}])

    def test_top_n(self):
        data = textwrap.dedent('''
            1.1.1.1 - - [10/Oct/2023:13:55:36 +0000] "GET /a HTTP/1.1" 200 100
            1.1.1.1 - - [10/Oct/2023:13:55:36 +0000] "GET /b HTTP/1.1" 200 100
            2.2.2.2 - - [10/Oct/2023:13:55:36 +0000] "GET /a HTTP/1.1" 200 100
            3.3.3.3 - - [10/Oct/2023:13:55:36 +0000] "GET /c HTTP/1.1" 200 100
        ''')
        with open('top.log', 'w') as f:
            f.write(data)
        r = self.run_logstat(['top.log', '--top', '2', '--json'])
        out = json.loads(r.stdout)
        self.assertEqual(out['top_ips'], [{'ip':'1.1.1.1','count':2},{'ip':'2.2.2.2','count':1}])

if __name__ == '__main__':
    unittest.main()
