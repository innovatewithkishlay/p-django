from datetime import datetime, timezone

from django.test import SimpleTestCase

from dna.analyzer import dependencies, git, scoring, security, source
from dna.analyzer.languages import is_test_path, language_for


PY = '''
"""Module docstring."""
import os


def simple():
    return 1


def branchy(x, items):
    # a comment
    if x > 1 and x < 10:
        for i in items:
            if i:
                return i
    elif x == 0:
        return None
    try:
        pass
    except ValueError:
        pass
    return [y for y in items if y]


class Thing:
    def method(self):
        while True:
            break
'''

JS = '''
// helper
function add(a, b) {
  return a + b;
}

const pick = (x) => {
  if (x && x.y) { return x.y; }
  return x ? 1 : 2;
};

class Box {
  open(lid) {
    for (const p of lid) { if (p) { console.log("{ not a brace }"); } }
  }
}
'''


class SourceAnalysisTests(SimpleTestCase):
    def test_python_functions_and_complexity(self):
        report = source.analyze_file("pkg/mod.py", PY, language_for("pkg/mod.py"))
        names = {f.name: f.complexity for f in report.functions}
        self.assertEqual(names["simple"], 1)
        # if + and + for + if + elif + except + comprehension(1 + 1 if) = 8, +1 base
        self.assertEqual(names["branchy"], 9)
        self.assertIn("Thing.method", names)
        self.assertEqual(names["Thing.method"], 2)
        self.assertGreater(report.comment_lines, 0)
        self.assertGreater(report.code_lines, 10)

    def test_javascript_functions_ignore_braces_in_strings(self):
        report = source.analyze_file("src/a.js", JS, language_for("src/a.js"))
        names = {f.name: f.complexity for f in report.functions}
        self.assertEqual(set(names), {"add", "pick", "open"})
        self.assertEqual(names["add"], 1)
        self.assertEqual(names["pick"], 4)  # if, &&, ?, +1
        self.assertEqual(names["open"], 3)
        self.assertEqual(report.comment_lines, 1)

    def test_broken_python_still_counts_lines(self):
        report = source.analyze_file("x.py", "def broken(:\n    if x:\n        pass\n", language_for("x.py"))
        self.assertEqual(report.functions, [])
        self.assertEqual(report.code_lines, 3)

    def test_maintainability_is_bounded(self):
        self.assertEqual(source.maintainability_index(0, 0, 0), 100.0)
        self.assertGreaterEqual(source.maintainability_index(10 ** 9, 500, 10 ** 6), 0)


class LanguageTests(SimpleTestCase):
    def test_test_paths(self):
        for path in ["tests/test_x.py", "pkg/test_views.py", "src/a.spec.ts", "web/__tests__/a.js",
                     "foo_test.go", "src/test/java/FooTest.java"]:
            self.assertTrue(is_test_path(path), path)
        for path in ["src/app.py", "lib/contest.js", "testing_utils_note.md"]:
            self.assertFalse(is_test_path(path), path)


class SecurityTests(SimpleTestCase):
    def ids(self, path, text, language="Python", is_test=False):
        return {h.rule.id for h in security.scan(path, text, language, is_test)}

    def test_real_looking_secret_is_flagged(self):
        self.assertIn("hardcoded-secret", self.ids("app/conf.py", 'API_KEY = "sk_live_9fA27xQpLm03ZtR8"\n'))

    def test_placeholders_and_words_are_not_flagged(self):
        self.assertNotIn("hardcoded-secret", self.ids("app/conf.py", 'password = "changeme123"\n'))
        self.assertNotIn("hardcoded-secret", self.ids("app/conf.py", 'secret_key="secret-key-value"\n'))
        self.assertNotIn("hardcoded-secret", self.ids("app/conf.py", 'API_KEY = os.environ["API_KEY"]\n'))

    def test_python_rules(self):
        found = self.ids("app/x.py", "subprocess.run(cmd, shell=True)\neval(user)\nyaml.load(f)\nhashlib.md5(b)\n")
        self.assertTrue({"py-shell", "py-eval", "py-yaml", "weak-hash"} <= found)

    def test_code_rules_skip_tests_but_keys_do_not(self):
        self.assertNotIn("py-eval", self.ids("tests/test_x.py", "eval(x)\n", is_test=True))
        self.assertIn("aws-key", self.ids("tests/test_x.py", "k = 'AKIAABCDEFGHIJKLMNOP'\n", is_test=True))

    def test_django_debug_only_in_settings(self):
        self.assertIn("django-debug", self.ids("proj/settings.py", "DEBUG = True\n"))
        self.assertNotIn("django-debug", self.ids("proj/views.py", "DEBUG = True\n"))


class DependencyTests(SimpleTestCase):
    def test_requirements(self):
        deps = dependencies.parse_requirements("django==5.0\nrequests>=2\nsix\n# comment\nhttpx~=0.27\n", "requirements.txt")
        pins = {d["name"]: d["pin"] for d in deps}
        self.assertEqual(pins, {"django": "pinned", "requests": "unpinned", "six": "unpinned", "httpx": "ranged"})
        self.assertTrue(next(d for d in deps if d["name"] == "six")["risk"])

    def test_package_json(self):
        deps = dependencies.parse_package_json(
            '{"dependencies": {"react": "^18.2.0", "request": "2.88.0", "x": "*"}, "devDependencies": {"jest": "29.0.0"}}',
            "package.json",
        )
        by = {d["name"]: d for d in deps}
        self.assertEqual(by["react"]["pin"], "ranged")
        self.assertEqual(by["x"]["pin"], "unpinned")
        self.assertTrue(by["request"]["risk"])
        self.assertTrue(by["jest"]["dev"])

    def test_go_mod(self):
        deps = dependencies.parse_go_mod("module x\n\nrequire (\n\tgithub.com/a/b v1.2.3\n)\nrequire c.io/d v0.1.0\n", "go.mod")
        self.assertEqual([d["name"] for d in deps], ["github.com/a/b", "c.io/d"])
        self.assertTrue(all(d["pin"] == "pinned" for d in deps))


class GitHelperTests(SimpleTestCase):
    def test_parse_repo_input(self):
        self.assertEqual(git.parse_repo_input("https://github.com/pallets/flask")[1:3], ("pallets", "flask"))
        self.assertEqual(git.parse_repo_input("github.com/pallets/flask.git")[1:3], ("pallets", "flask"))
        self.assertEqual(git.parse_repo_input("pallets/flask")[1:3], ("pallets", "flask"))
        with self.assertRaises(ValueError):
            git.parse_repo_input("not a repo at all")

    def test_sampling_keeps_ends_and_prefers_tags(self):
        commits = [git.Commit(f"{i:040x}", "a", datetime(2020, 1, 1, tzinfo=timezone.utc), "m") for i in range(100)]
        commits[48].tag = "v1.0"
        picked = git.sample_commits(commits, 5)
        self.assertEqual(len(picked), 5)
        self.assertIs(picked[0], commits[0])
        self.assertIs(picked[-1], commits[-1])
        self.assertIn(commits[48], picked)
        self.assertEqual(git.sample_commits(commits[:3], 5), commits[:3])


class ScoringTests(SimpleTestCase):
    def base(self, **kw):
        m = dict(
            function_count=100, complex_functions=2, long_functions=1, source_files=20, large_files=0,
            avg_complexity=2.5, max_nesting=3, avg_maintainability=60, duplication_ratio=0.02,
            test_files=10, test_ratio=0.6, has_ci=True, security_by_severity={}, code_lines=5000,
            dependency_count=10, unpinned_dependencies=0, risky_dependencies=0,
            has_readme=True, readme_lines=50, comment_ratio=0.15, has_license=True,
        )
        m.update(kw)
        return m

    def test_healthy_project_scores_high(self):
        scores = scoring.compute(self.base())
        self.assertGreaterEqual(scores.health, 85)

    def test_critical_security_caps_score(self):
        scores = scoring.compute(self.base(security_by_severity={"critical": 1}))
        self.assertLessEqual(scores.security, 45)

    def test_no_tests_means_zero_testing(self):
        self.assertEqual(scoring.compute(self.base(test_files=0, has_ci=False)).testing, 0)

    def test_scale(self):
        self.assertEqual(scoring.scale(2, 2, 10), 100)
        self.assertEqual(scoring.scale(10, 2, 10), 0)
        self.assertEqual(scoring.scale(0.7, 0.7, 0.0), 100)
        self.assertEqual(scoring.hotspot(0, 10, 10, 10), 0)
