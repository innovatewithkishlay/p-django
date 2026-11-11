"""
The analysis pipeline: clone → sample history → measure → score → explain.

``start_analysis`` is what views call; it records an ``AnalysisRun`` and
does the work in a background thread (or inline when ``DNA_ASYNC`` is off,
which is what the tests and the management command use).
"""

import logging
import threading
import traceback
from collections import Counter
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from . import collector, git, insights, scoring
from .source import COMPLEX_FUNCTION_CC, LONG_FUNCTION_LINES

log = logging.getLogger("dna")
LARGE_FILE_LINES = 500


def start_analysis(repository, reanalyze=False, max_snapshots=None, run_async=None):
    from dna.models import AnalysisRun

    active = repository.active_run
    if active:
        return active
    run = AnalysisRun.objects.create(repository=repository, stage="Waiting to start")
    run_async = settings.DNA_ASYNC if run_async is None else run_async
    if run_async:
        thread = threading.Thread(
            target=_run_in_thread, args=(run.pk, reanalyze, max_snapshots), daemon=True, name=f"dna-{run.pk}"
        )
        thread.start()
    else:
        execute(run, reanalyze=reanalyze, max_snapshots=max_snapshots)
    return run


def _run_in_thread(run_id, reanalyze, max_snapshots):
    from dna.models import AnalysisRun

    close_old_connections()
    try:
        execute(AnalysisRun.objects.get(pk=run_id), reanalyze=reanalyze, max_snapshots=max_snapshots)
    finally:
        close_old_connections()


def _update(run, **fields):
    for key, value in fields.items():
        setattr(run, key, value)
    run.save(update_fields=list(fields))


def execute(run, reanalyze=False, max_snapshots=None):
    repo = run.repository
    max_snapshots = max_snapshots or settings.DNA_MAX_SNAPSHOTS
    _update(run, status=run.Status.RUNNING, stage="Cloning repository", progress=3)
    try:
        workdir = settings.DNA_WORKDIR / repo.slug
        clone_url = repo.url if repo.source != repo.Source.GITHUB else f"https://github.com/{repo.owner}/{repo.name}.git"
        git.clone_or_update(clone_url, workdir)
        branch = git.default_branch(workdir)
        repo.default_branch = branch
        repo.save(update_fields=["default_branch"])

        _update(run, stage="Reading history", progress=8)
        commits = git.history(workdir, "HEAD")
        if not commits:
            raise git.GitError("The repository has no commits yet.")

        if reanalyze:
            repo.snapshots.all().delete()
        existing = set(repo.snapshots.values_list("commit_sha", flat=True))
        sampled = git.sample_commits(commits, max_snapshots)
        todo = [c for c in sampled if c.sha not in existing]
        if not todo:
            _update(run, stage="Already up to date", progress=100, status=run.Status.DONE, finished_at=timezone.now())
            repo.last_analyzed_at = timezone.now()
            repo.save(update_fields=["last_analyzed_at"])
            return run

        for index, commit in enumerate(todo, start=1):
            label = commit.tag or commit.sha[:7]
            _update(run, stage=f"Analysing {label} ({index} of {len(todo)})", progress=10 + int(85 * (index - 1) / len(todo)))
            analyze_commit(repo, workdir, commit)
            run.snapshots_created = index
            run.save(update_fields=["snapshots_created"])

        git.checkout(workdir, commits[-1].sha)
        latest = repo.snapshots.order_by("-committed_at").first()
        if latest and latest.languages:
            repo.primary_language = next(iter(latest.languages))
        repo.last_analyzed_at = timezone.now()
        repo.save(update_fields=["primary_language", "last_analyzed_at"])
        _update(run, status=run.Status.DONE, stage="Finished", progress=100, finished_at=timezone.now())
    except Exception as exc:  # report every failure to the UI
        log.error("Analysis of %s failed: %s\n%s", repo, exc, traceback.format_exc())
        message = str(exc) if isinstance(exc, git.GitError) else f"{type(exc).__name__}: {exc}"
        _update(run, status=run.Status.FAILED, error=message, stage="Failed", finished_at=timezone.now())
    return run


def aggregate(metrics):
    source_files = metrics.source_files
    functions = metrics.all_functions
    code_lines = sum(f.code_lines for f in metrics.files)
    comment_lines = sum(f.comment_lines for f in metrics.files)
    test_lines = sum(f.code_lines for f in metrics.test_files)
    source_lines = sum(f.code_lines for f in source_files)
    mi_weight = sum(max(f.code_lines, 1) for f in source_files)
    severities = Counter(h.rule.severity for h in metrics.security_hits)
    return {
        "total_files": len(metrics.files),
        "source_files": len(source_files),
        "code_lines": code_lines,
        "comment_lines": comment_lines,
        "blank_lines": sum(f.blank_lines for f in metrics.files),
        "function_count": len(functions),
        "avg_complexity": round(sum(fn.complexity for _, fn in functions) / len(functions), 2) if functions else 0.0,
        "max_complexity": max((fn.complexity for _, fn in functions), default=0),
        "complex_functions": sum(1 for _, fn in functions if fn.complexity > COMPLEX_FUNCTION_CC),
        "long_functions": sum(1 for _, fn in functions if fn.length > LONG_FUNCTION_LINES),
        "large_files": sum(1 for f in source_files if f.code_lines > LARGE_FILE_LINES),
        "avg_maintainability": round(
            sum(f.maintainability * max(f.code_lines, 1) for f in source_files) / mi_weight, 1
        ) if source_files else 100.0,
        "duplication_ratio": metrics.duplication_ratio,
        "max_nesting": max((f.max_nesting for f in source_files), default=0),
        "test_files": len(metrics.test_files),
        "test_lines": test_lines,
        "test_ratio": round(test_lines / source_lines, 3) if source_lines else 0.0,
        "dependency_count": len(metrics.dependencies),
        "unpinned_dependencies": sum(1 for d in metrics.dependencies if d["pin"] == "unpinned"),
        "risky_dependencies": sum(1 for d in metrics.dependencies if d["risk"]),
        "security_issues": len(metrics.security_hits),
        "security_by_severity": dict(severities),
        "todo_count": sum(f.todo_count for f in metrics.files),
        "has_readme": metrics.has_readme,
        "has_ci": metrics.has_ci,
        "has_license": metrics.has_license,
        "readme_lines": metrics.readme_lines,
        "comment_ratio": round(comment_lines / (code_lines + comment_lines), 3) if code_lines else 0.0,
    }


SNAPSHOT_FIELDS = [
    "total_files", "code_lines", "comment_lines", "blank_lines", "function_count",
    "avg_complexity", "max_complexity", "complex_functions", "long_functions", "large_files",
    "avg_maintainability", "duplication_ratio", "max_nesting", "test_files", "test_lines", "test_ratio",
    "dependency_count", "unpinned_dependencies", "security_issues", "todo_count",
    "has_readme", "has_ci", "has_license", "comment_ratio",
]


def analyze_commit(repo, workdir, commit):
    from dna.models import FileMetric, Finding, Snapshot

    git.checkout(workdir, commit.sha)
    paths = git.tracked_files(workdir)
    metrics = collector.collect(workdir, paths, settings.DNA_MAX_FILE_BYTES)
    changes, authors = git.churn(workdir, commit.sha)
    numbers = aggregate(metrics)
    scores = scoring.compute(numbers)

    hot_functions = sorted(
        ({"name": fn.name, "path": path, "line": fn.line, "complexity": fn.complexity, "length": fn.length}
         for path, fn in metrics.all_functions),
        key=lambda f: -f["complexity"],
    )[:15]

    committed_at = commit.date
    if timezone.is_naive(committed_at):
        committed_at = committed_at.replace(tzinfo=dt_timezone.utc)

    with transaction.atomic():
        snapshot = Snapshot.objects.create(
            repository=repo,
            commit_sha=commit.sha,
            commit_message=commit.message,
            author=commit.author,
            committed_at=committed_at,
            tag=commit.tag,
            languages=metrics.languages(),
            modules=metrics.modules(),
            dependencies=metrics.dependencies[:200],
            hot_functions=hot_functions,
            score_complexity=scores.complexity,
            score_maintainability=scores.maintainability,
            score_testing=scores.testing,
            score_security=scores.security,
            score_dependencies=scores.dependencies,
            score_documentation=scores.documentation,
            health_score=scores.health,
            **{k: numbers[k] for k in SNAPSHOT_FIELDS},
        )

        security_per_file = Counter(h.path for h in metrics.security_hits)
        max_churn = max((changes.get(f.path, 0) for f in metrics.source_files), default=0)
        max_cx = max((f.complexity for f in metrics.source_files), default=0)
        rows = []
        for f in metrics.files:
            top = f.max_function
            is_test = f.path in metrics.test_paths
            churn = changes.get(f.path, 0)
            rows.append(FileMetric(
                snapshot=snapshot,
                path=f.path[:500],
                language=f.language,
                code_lines=f.code_lines,
                comment_lines=f.comment_lines,
                function_count=f.function_count,
                complexity=f.complexity,
                max_function_complexity=top.complexity if top else 0,
                max_function_name=(top.name[:200] if top else ""),
                maintainability=f.maintainability,
                max_nesting=min(f.max_nesting, 32767),
                churn=churn,
                authors=min(authors.get(f.path, 0), 32767),
                hotspot_score=0 if is_test else scoring.hotspot(churn, f.complexity, max_churn, max_cx),
                is_test=is_test,
                security_issues=security_per_file.get(f.path, 0),
                todo_count=min(f.todo_count, 32767),
            ))
        FileMetric.objects.bulk_create(rows, batch_size=500)

        previous = repo.snapshots.filter(committed_at__lt=snapshot.committed_at).exclude(pk=snapshot.pk) \
            .order_by("-committed_at").first()
        first = repo.snapshots.exclude(pk=snapshot.pk).order_by("committed_at").first()
        if first and first.committed_at > snapshot.committed_at:
            first = None
        ctx = insights.Context(
            snapshot=snapshot,
            previous=previous,
            first=first,
            files={fm.path: fm for fm in rows},
            previous_files={fm.path: fm for fm in previous.files.all()} if previous else {},
            security_hits=metrics.security_hits,
            dependencies=metrics.dependencies,
        )
        Finding.objects.bulk_create([
            Finding(
                snapshot=snapshot, category=i.category, severity=i.severity, title=i.title[:250],
                explanation=i.explanation, recommendation=i.recommendation, file_path=i.file_path[:500],
                line=i.line, priority=i.priority, is_regression=i.is_regression,
            )
            for i in insights.generate(ctx)
        ])
        snapshot.summary = insights.summarize(ctx)
        snapshot.save(update_fields=["summary"])
    return snapshot
