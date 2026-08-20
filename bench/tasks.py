"""Benchmark tasks.

Every task is verified by running code, never by reading the model's prose.
A task passes only if the repository ends up in a state a reviewer would
accept — tests green, behaviour correct, and nothing collateral destroyed.

Each task is a dict:
    name     short id
    skill    what it is measuring
    prompt   what the agent is told
    setup    fn(dir) -> None, writes the starting files
    verify   fn(dir) -> (bool, str)
"""

import json
import os
import subprocess
import sys


def write(directory, name, text):
    path = os.path.join(directory, name)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def run(directory, *args, timeout=60):
    """Run a python command in the task directory."""
    try:
        proc = subprocess.run(
            [sys.executable] + list(args), cwd=directory, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return 1, "timed out"
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def tests_pass(directory, expect_count=None):
    code, out = run(directory, "-m", "unittest", "discover", "-v")
    if code != 0:
        tail = "\n".join(out.strip().splitlines()[-6:])
        return False, "tests failed:\n%s" % tail
    if expect_count is not None:
        marker = "Ran %d test" % expect_count
        if marker not in out:
            got = [l for l in out.splitlines() if l.startswith("Ran ")]
            return False, "expected %d tests, got %r" % (expect_count, got)
    return True, "tests pass"


# --- 1. fix a bug the tests already catch -----------------------------------

def setup_fix_bug(d):
    write(d, "stats.py",
          "def average(numbers):\n"
          "    total = 0\n"
          "    for n in numbers:\n"
          "        total += n\n"
          "    return total / len(numbers)\n"
          "\n"
          "\n"
          "def median(numbers):\n"
          "    s = sorted(numbers)\n"
          "    mid = len(s) // 2\n"
          "    return s[mid]\n")
    write(d, "test_stats.py",
          "import unittest\n"
          "from stats import average, median\n"
          "\n"
          "\n"
          "class T(unittest.TestCase):\n"
          "    def test_average(self):\n"
          "        self.assertEqual(average([1, 2, 3]), 2)\n"
          "\n"
          "    def test_average_empty(self):\n"
          "        self.assertEqual(average([]), 0)\n"
          "\n"
          "    def test_median_odd(self):\n"
          "        self.assertEqual(median([3, 1, 2]), 2)\n"
          "\n"
          "    def test_median_even(self):\n"
          "        self.assertEqual(median([1, 2, 3, 4]), 2.5)\n")


def verify_fix_bug(d):
    return tests_pass(d, expect_count=4)


# --- 2. add a feature and wire it through -----------------------------------

def setup_add_feature(d):
    write(d, "expenses.py",
          "def total(rows, category=None):\n"
          "    return sum(r['amount'] for r in rows)\n")
    write(d, "main.py",
          "import argparse\n"
          "import json\n"
          "import sys\n"
          "\n"
          "import expenses\n"
          "\n"
          "ROWS = [\n"
          "    {'amount': 100.0, 'category': 'food'},\n"
          "    {'amount': 250.0, 'category': 'travel'},\n"
          "    {'amount': 50.0, 'category': 'food'},\n"
          "]\n"
          "\n"
          "\n"
          "def main():\n"
          "    parser = argparse.ArgumentParser()\n"
          "    parser.add_argument('--category')\n"
          "    args = parser.parse_args()\n"
          "    print('%.2f' % expenses.total(ROWS))\n"
          "\n"
          "\n"
          "if __name__ == '__main__':\n"
          "    main()\n")


def verify_add_feature(d):
    code, out = run(d, "main.py", "--category", "food")
    if code != 0:
        return False, "main.py --category food failed: %s" % out.strip()[:200]
    if "150" not in out:
        return False, "expected 150.00 for food, got %r" % out.strip()[:100]
    code, out = run(d, "main.py")
    if code != 0 or "400" not in out:
        return False, "unfiltered total broke: %r" % out.strip()[:100]
    return True, "filter works, unfiltered total intact"


# --- 3. rename across several files -----------------------------------------

def setup_multi_file(d):
    write(d, "core/__init__.py", "")
    write(d, "core/calc.py",
          "def compute_tax(amount):\n"
          "    return amount * 0.18\n")
    write(d, "billing.py",
          "from core.calc import compute_tax\n"
          "\n"
          "\n"
          "def invoice(amount):\n"
          "    return amount + compute_tax(amount)\n")
    write(d, "report.py",
          "from core.calc import compute_tax\n"
          "\n"
          "\n"
          "def summary(amounts):\n"
          "    return [compute_tax(a) for a in amounts]\n")
    write(d, "test_billing.py",
          "import unittest\n"
          "\n"
          "import billing\n"
          "import report\n"
          "\n"
          "\n"
          "class T(unittest.TestCase):\n"
          "    def test_invoice(self):\n"
          "        self.assertAlmostEqual(billing.invoice(100), 118.0)\n"
          "\n"
          "    def test_summary(self):\n"
          "        self.assertAlmostEqual(report.summary([100])[0], 18.0)\n")


def verify_multi_file(d):
    calc = read(d, "core/calc.py")
    if "def gst" not in calc:
        return False, "core/calc.py still has no gst function"
    if "compute_tax" in calc:
        return False, "old name compute_tax still defined in core/calc.py"
    for name in ("billing.py", "report.py"):
        body = read(d, name)
        if "compute_tax" in body:
            return False, "%s still references compute_tax" % name
        if "gst" not in body:
            return False, "%s was not updated to gst" % name
    return tests_pass(d, expect_count=2)


# --- 4. answer a question about the code ------------------------------------

def setup_find_answer(d):
    write(d, "app/__init__.py", "")
    write(d, "app/auth.py",
          "SESSION_TIMEOUT = 900\n"
          "\n"
          "\n"
          "def login(user, password):\n"
          "    return _verify(user, password)\n"
          "\n"
          "\n"
          "def _verify(user, password):\n"
          "    return bool(user and password)\n")
    write(d, "app/handlers.py",
          "from app.auth import login\n"
          "\n"
          "\n"
          "def post_login(request):\n"
          "    return login(request['user'], request['password'])\n")
    for i in range(6):
        write(d, "app/noise%d.py" % i,
              "def helper%d():\n    return %d\n" % (i, i))


def verify_find_answer(d, answer=""):
    lowered = answer.lower()
    if "900" not in lowered:
        return False, "answer does not mention the value 900"
    if "auth.py" not in lowered:
        return False, "answer does not cite app/auth.py"
    return True, "found the constant and cited the file"


# --- 5. add tests without deleting the existing ones ------------------------

def setup_write_tests(d):
    write(d, "strings.py",
          "def slugify(text):\n"
          "    return text.strip().lower().replace(' ', '-')\n"
          "\n"
          "\n"
          "def titlecase(text):\n"
          "    return ' '.join(w.capitalize() for w in text.split())\n")
    write(d, "test_strings.py",
          "import unittest\n"
          "\n"
          "from strings import slugify\n"
          "\n"
          "\n"
          "class SlugifyTest(unittest.TestCase):\n"
          "    def test_basic(self):\n"
          "        self.assertEqual(slugify(' Hello World '), 'hello-world')\n")


def verify_write_tests(d):
    body = read(d, "test_strings.py")
    if "test_basic" not in body:
        return False, "the pre-existing test_basic was deleted"
    if "titlecase" not in body:
        return False, "no test was added for titlecase"
    ok, message = tests_pass(d)
    if not ok:
        return False, message
    code, out = run(d, "-m", "unittest", "discover", "-v")
    ran = [l for l in out.splitlines() if l.startswith("Ran ")]
    count = int(ran[0].split()[1]) if ran else 0
    if count < 2:
        return False, "expected at least 2 tests, found %d" % count
    return True, "%d tests, original kept" % count


# --- 6. edit structured data correctly --------------------------------------

def setup_json_config(d):
    write(d, "config.json", json.dumps({
        "name": "billing-service",
        "version": "1.2.0",
        "retries": 3,
        "endpoints": {"api": "http://localhost:8080", "metrics": "http://localhost:9090"},
        "features": ["invoices", "reports"],
    }, indent=2) + "\n")


def verify_json_config(d):
    raw = read(d, "config.json")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return False, "config.json is no longer valid JSON: %s" % exc
    if data.get("retries") != 5:
        return False, "retries is %r, expected 5" % data.get("retries")
    if data.get("endpoints", {}).get("api") != "https://api.example.com":
        return False, "api endpoint is %r" % data.get("endpoints", {}).get("api")
    if "audit" not in (data.get("features") or []):
        return False, "audit was not added to features"
    for key in ("invoices", "reports"):
        if key not in (data.get("features") or []):
            return False, "existing feature %r was dropped" % key
    if data.get("version") != "1.2.0" or data.get("name") != "billing-service":
        return False, "unrelated fields were changed"
    return True, "all four edits correct, nothing else touched"


# --- 7. refactor without changing behaviour ---------------------------------

def setup_refactor(d):
    write(d, "shapes.py",
          "def area_rect(w, h):\n"
          "    if w <= 0 or h <= 0:\n"
          "        raise ValueError('dimensions must be positive')\n"
          "    return w * h\n"
          "\n"
          "\n"
          "def area_tri(b, h):\n"
          "    if b <= 0 or h <= 0:\n"
          "        raise ValueError('dimensions must be positive')\n"
          "    return 0.5 * b * h\n"
          "\n"
          "\n"
          "def area_para(b, h):\n"
          "    if b <= 0 or h <= 0:\n"
          "        raise ValueError('dimensions must be positive')\n"
          "    return b * h\n")
    write(d, "test_shapes.py",
          "import unittest\n"
          "\n"
          "import shapes\n"
          "\n"
          "\n"
          "class T(unittest.TestCase):\n"
          "    def test_values(self):\n"
          "        self.assertEqual(shapes.area_rect(2, 3), 6)\n"
          "        self.assertEqual(shapes.area_tri(2, 3), 3.0)\n"
          "        self.assertEqual(shapes.area_para(2, 3), 6)\n"
          "\n"
          "    def test_validation(self):\n"
          "        for fn in (shapes.area_rect, shapes.area_tri, shapes.area_para):\n"
          "            with self.assertRaises(ValueError):\n"
          "                fn(0, 1)\n")


def verify_refactor(d):
    body = read(d, "shapes.py")
    guards = body.count("must be positive")
    if guards > 1:
        return False, "the validation is still duplicated %d times" % guards
    if guards == 0:
        return False, "the validation message disappeared entirely"
    return tests_pass(d, expect_count=2)


# --- 8. use the shell to answer a counting question -------------------------

def setup_shell_count(d):
    for i in range(7):
        write(d, "src/mod%d.py" % i, "# module %d\nVALUE = %d\n" % (i, i))
    for i in range(3):
        write(d, "docs/note%d.md" % i, "# note %d\n" % i)
    write(d, "src/deep/extra.py", "X = 1\n")


def verify_shell_count(d, answer=""):
    if "8" not in answer:
        return False, "answer does not contain 8 (there are 8 .py files)"
    return True, "counted correctly"


# --- 9. repair a file that does not parse -----------------------------------

def setup_syntax_error(d):
    write(d, "parser.py",
          "import json\n"
          "\n"
          "\n"
          "def load(text):\n"
          "    data = json.loads(text)\n"
          "    if not isinstance(data, dict)\n"
          "        raise TypeError('expected an object')\n"
          "    return data\n"
          "\n"
          "\n"
          "def keys(text):\n"
          "    return sorted(load(text).keys()\n")
    write(d, "test_parser.py",
          "import unittest\n"
          "\n"
          "import parser as p\n"
          "\n"
          "\n"
          "class T(unittest.TestCase):\n"
          "    def test_load(self):\n"
          "        self.assertEqual(p.load('{\"a\": 1}'), {'a': 1})\n"
          "\n"
          "    def test_keys(self):\n"
          "        self.assertEqual(p.keys('{\"b\": 1, \"a\": 2}'), ['a', 'b'])\n"
          "\n"
          "    def test_rejects_list(self):\n"
          "        with self.assertRaises(TypeError):\n"
          "            p.load('[1, 2]')\n")


def verify_syntax_error(d):
    return tests_pass(d, expect_count=3)


# --- 10. restraint: answer without touching anything ------------------------

def setup_no_op(d):
    write(d, "server.py",
          "PORT = 8080\n"
          "DEBUG = False\n"
          "\n"
          "\n"
          "def start():\n"
          "    return 'listening on %d' % PORT\n")
    write(d, "README.md", "# Service\n\nRun with `python server.py`.\n")


def verify_no_op(d, answer=""):
    if read(d, "server.py") != ("PORT = 8080\nDEBUG = False\n\n\n"
                                "def start():\n"
                                "    return 'listening on %d' % PORT\n"):
        return False, "server.py was modified when only a question was asked"
    if read(d, "README.md") != "# Service\n\nRun with `python server.py`.\n":
        return False, "README.md was modified when only a question was asked"
    extra = [n for n in os.listdir(d) if n not in ("server.py", "README.md")]
    if extra:
        return False, "created unrequested files: %s" % ", ".join(extra[:5])
    if "8080" not in answer:
        return False, "the question itself was not answered"
    return True, "answered without touching the tree"


TASKS = [
    {"name": "fix_bug", "skill": "debug from a failing test",
     "setup": setup_fix_bug, "verify": verify_fix_bug,
     "prompt": "The tests in this project fail. Find out why, fix stats.py, "
               "and run the tests again to confirm they all pass."},

    {"name": "add_feature", "skill": "add and wire a feature",
     "setup": setup_add_feature, "verify": verify_add_feature,
     "prompt": "The --category flag on main.py is accepted but ignored. Make it "
               "work: expenses.total should filter by category, and main.py "
               "should pass the flag through. Running with no flag must still "
               "print the total of everything."},

    {"name": "multi_file", "skill": "rename across files",
     "setup": setup_multi_file, "verify": verify_multi_file,
     "prompt": "Rename the function compute_tax to gst everywhere in this "
               "project, including all call sites. Then run the tests."},

    {"name": "find_answer", "skill": "search and cite",
     "setup": setup_find_answer, "verify": verify_find_answer, "asks": True,
     "prompt": "How long is a session valid for in this codebase, and which "
               "file defines it? Answer in one line."},

    {"name": "write_tests", "skill": "add tests, keep existing",
     "setup": setup_write_tests, "verify": verify_write_tests,
     "prompt": "Add unit tests for the titlecase function in strings.py. Keep "
               "every test that already exists. Then run the suite."},

    {"name": "json_config", "skill": "precise structured edit",
     "setup": setup_json_config, "verify": verify_json_config,
     "prompt": "In config.json: set retries to 5, change the api endpoint to "
               "https://api.example.com, and add \"audit\" to the features "
               "list. Change nothing else."},

    {"name": "refactor", "skill": "remove duplication safely",
     "setup": setup_refactor, "verify": verify_refactor,
     "prompt": "The same validation is copy-pasted in all three functions in "
               "shapes.py. Extract it into one helper so the check appears "
               "once. Behaviour must not change; run the tests to prove it."},

    {"name": "shell_count", "skill": "use the shell",
     "setup": setup_shell_count, "verify": verify_shell_count, "asks": True,
     "prompt": "How many .py files are in this project, counting every "
               "subdirectory? Answer with just the number and nothing else."},

    {"name": "syntax_error", "skill": "repair broken syntax",
     "setup": setup_syntax_error, "verify": verify_syntax_error,
     "prompt": "parser.py does not even parse. Fix the syntax errors so the "
               "test suite runs and passes."},

    {"name": "no_op", "skill": "restraint",
     "setup": setup_no_op, "verify": verify_no_op, "asks": True,
     "prompt": "What port does this server listen on? Just tell me — do not "
               "change any files."},
]
