import unittest
import subprocess, json, os, textwrap

class TestLogstat(unittest.TestCase):
    def run_logstat(self, args, input_data=None):
        cmd = ['python', 'logstat.py'] + args
        result = subprocess.run(cmd, input=input_data, text=True, capture_output=True)
        return result

    def test_basic(self):
        log = textwrap.dedent('''
            127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
            127.0.0.1 - - [10/Oct/2023:13:56:01 +0000] "POST /submit HTTP/1.1" 404 123
            1.2.3.4 - - [10/Oct/2023:13:57:12 +0000] "GET /index.html HTTP/1.1" 200 0
        ''').strip()
        res = self.run_logstat(['--json'], input_data=log)
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout)
        self.assertEqual(out['total_requests'], 3)
        self.assertEqual(out['total_bytes'], 2326+123+0)
        self.assertEqual(out['status_counts'], {'200':2,'404':1})
        self.assertEqual(out['malformed_lines'],0)
        self.assertEqual(out['top_ips'], [{'ip':'127.0.0.1','count':2},{'ip':'1.2.3.4','count':1}])
        self.assertEqual(out['top_paths'], [{'path':'/index.html','count':2},{'path':'/submit','count':1}])

    def test_filters(self):
        log = textwrap.dedent('''
            127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
            127.0.0.1 - - [10/Oct/2023:13:56:01 +0000] "POST /submit HTTP/1.1" 404 123
            1.2.3.4 - - [10/Oct/2023:13:57:12 +0000] "GET /index.html HTTP/1.1" 200 0
        ''').strip()
        res = self.run_logstat(['--status','200','--method','GET','--json'], input_data=log)
        out=json.loads(res.stdout)
        self.assertEqual(out['total_requests'],2)
        self.assertEqual(out['status_counts'],{'200':2})
        self.assertEqual(out['top_ips'], [{'ip':'127.0.0.1','count':1},{'ip':'1.2.3.4','count':1}])

    def test_malformed(self):
        log = textwrap.dedent('''
            bad line
            127.0.0.1 - - [10/Oct/2023:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326
            another bad line
        ''').strip()
        res = self.run_logstat(['--json'], input_data=log)
        out=json.loads(res.stdout)
        self.assertEqual(out['malformed_lines'],2)
        self.assertEqual(out['total_requests'],1)

    def test_top_n(self):
        log = '\n'.join([f"127.0.0.{i} - - [10/Oct/2023:13:55:36 +0000] \"GET /path{i} HTTP/1.1\" 200 100" for i in range(10)])
        res = self.run_logstat(['--top','3','--json'], input_data=log)
        out=json.loads(res.stdout)
        self.assertEqual(len(out['top_ips']),3)
        self.assertEqual(len(out['top_paths']),3)

if __name__ == '__main__':
    unittest.main()
