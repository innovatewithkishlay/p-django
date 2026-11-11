"""End-to-end: build a tiny git repository, analyse it, and render every page."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from dna.analyzer.pipeline import start_analysis
from dna.models import Repository

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def sh(cwd, *args, date=None):
    env = dict(GIT_ENV)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def make_repo(path):
    sh(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("# Demo\n\nA demo project.\n" + "line\n" * 25)
    (path / "app.py").write_text("def hello(name):\n    return 'hi ' + name\n")
    (path / "requirements.txt").write_text("django==5.0\n")
    sh(path, "add", "-A")
    sh(path, "commit", "-q", "-m", "first", date="2024-01-01T10:00:00+00:00")
    sh(path, "tag", "v0.1")

    body = "\n".join(
        f"def handler_{i}(x, y):\n"
        f"    if x and y:\n        for z in range(x):\n            if z % 2 or z % 3:\n                y += z\n"
        f"    elif x == {i}:\n        return None\n    return y\n"
        for i in range(12)
    )
    (path / "app.py").write_text(body + '\nAPI_TOKEN = "Zq81LmPx92TrVw4K"\n')
    (path / "requirements.txt").write_text("django==5.0\nrequests\nsix\n")
    sh(path, "add", "-A")
    sh(path, "commit", "-q", "-m", "add handlers", date="2024-06-01T10:00:00+00:00")

    (path / "tests").mkdir()
    (path / "tests" / "test_app.py").write_text("from app import hello\n\ndef test_hello():\n    assert hello('a') == 'hi a'\n")
    sh(path, "add", "-A")
    sh(path, "commit", "-q", "-m", "add tests", date="2024-09-01T10:00:00+00:00")


class PipelineTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.source = cls.tmp / "demo"
        cls.source.mkdir()
        make_repo(cls.source)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def analyse(self):
        repo = Repository.objects.create(name="demo", url=str(self.source), source=Repository.Source.LOCAL)
        with override_settings(DNA_WORKDIR=self.tmp / "work"):
            run = start_analysis(repo, run_async=False, max_snapshots=5)
        run.refresh_from_db()
        self.assertEqual(run.status, "done", run.error)
        return repo

    def test_snapshots_scores_and_findings(self):
        repo = self.analyse()
        snaps = list(repo.snapshots.all())
        self.assertEqual(len(snaps), 3)
        first, middle, last = snaps
        self.assertEqual(first.tag, "v0.1")
        self.assertGreater(middle.avg_complexity, first.avg_complexity)
        self.assertEqual(middle.unpinned_dependencies, 2)
        self.assertEqual(last.test_files, 1)
        self.assertTrue(last.has_readme)
        self.assertGreater(last.score_testing, middle.score_testing)

        titles = list(middle.findings.values_list("title", flat=True))
        self.assertTrue(any("Credential" in t for t in titles), titles)
        self.assertTrue(any("complexity rose" in t for t in titles), titles)
        self.assertTrue(middle.findings.filter(is_regression=True).exists())
        self.assertIn("health score", last.summary)

        # Re-running finds nothing new to do.
        with override_settings(DNA_WORKDIR=self.tmp / "work"):
            run = start_analysis(repo, run_async=False)
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertEqual(repo.snapshots.count(), 3)

    def test_every_page_renders(self):
        repo = self.analyse()
        last = repo.latest_snapshot
        first = repo.snapshots.first()
        urls = [
            reverse("dna:home"),
            reverse("dna:how"),
            reverse("dna:repo", args=[repo.slug]),
            reverse("dna:timeline", args=[repo.slug]),
            reverse("dna:hotspots", args=[repo.slug]),
            reverse("dna:findings", args=[repo.slug]) + "?category=security",
            reverse("dna:dependencies", args=[repo.slug]),
            reverse("dna:compare", args=[repo.slug]) + f"?a={last.short_sha}&b={first.short_sha}",
            reverse("dna:snapshot", args=[repo.slug, last.short_sha]) + "?sort=-churn&q=app",
            reverse("dna:status", args=[repo.slug]),
            reverse("dna:api_repo", args=[repo.slug]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("dna:snapshot", args=[repo.slug, "ffffff"])).status_code, 404)
        data = self.client.get(reverse("dna:api_repo", args=[repo.slug])).json()
        self.assertEqual(len(data["snapshots"]), 3)

    def test_home_form_validation(self):
        response = self.client.post(reverse("dna:home"), {"repo": "definitely not a repo"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "doesn&#x27;t look like a GitHub repository")

    def test_delete(self):
        repo = self.analyse()
        response = self.client.post(reverse("dna:delete", args=[repo.slug]))
        self.assertRedirects(response, reverse("dna:home"))
        self.assertFalse(Repository.objects.exists())
