from collections import Counter

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .analyzer.languages import LANGUAGE_COLORS
from .analyzer.pipeline import start_analysis
from .forms import RepositoryForm
from .models import FileMetric, Finding, Repository, Snapshot

EXAMPLES = ["pallets/itsdangerous", "psf/requests", "expressjs/express", "encode/httpx"]

# Metrics shown as cards, with the direction that counts as "better".
METRIC_CARDS = [
    ("code_lines", "Lines of code", "int", None),
    ("avg_complexity", "Avg. complexity", "float", "down"),
    ("avg_maintainability", "Maintainability index", "float", "up"),
    ("test_ratio", "Test / code ratio", "ratio", "up"),
    ("duplication_ratio", "Duplication", "pct", "down"),
    ("security_issues", "Risky patterns", "int", "down"),
    ("dependency_count", "Dependencies", "int", None),
    ("todo_count", "TODO / FIXME", "int", "down"),
]


# ---------------------------------------------------------------- helpers

def _repo(slug):
    return get_object_or_404(Repository, slug=slug)


def _snapshot(repo, sha):
    snapshot = repo.snapshots.filter(commit_sha__startswith=sha).first()
    if not snapshot:
        raise Http404("No snapshot for that commit.")
    return snapshot


def _metric_cards(current, previous):
    cards = []
    for field, label, kind, better in METRIC_CARDS:
        value = getattr(current, field)
        old = getattr(previous, field) if previous else None
        delta = None if old is None else value - old
        mood = "flat"
        if delta and better:
            improved = (delta < 0) if better == "down" else (delta > 0)
            mood = "good" if improved else "bad"
        cards.append({"label": label, "value": value, "kind": kind, "delta": delta, "mood": mood, "field": field})
    return cards


def _strand(snapshots):
    """Rows for the DNA strand: one column per snapshot, one cell per dimension."""
    return [
        {"snapshot": s, "cells": [(label, score) for label, score, _ in s.scores()]}
        for s in snapshots
    ]


def _chart_series(snapshots):
    return {
        "labels": [s.label for s in snapshots],
        "dates": [s.committed_at.strftime("%b %Y") for s in snapshots],
        "health": [s.health_score for s in snapshots],
        "complexity": [s.score_complexity for s in snapshots],
        "maintainability": [s.score_maintainability for s in snapshots],
        "testing": [s.score_testing for s in snapshots],
        "security": [s.score_security for s in snapshots],
        "dependencies": [s.score_dependencies for s in snapshots],
        "documentation": [s.score_documentation for s in snapshots],
        "code_lines": [s.code_lines - s.test_lines for s in snapshots],
        "test_lines": [s.test_lines for s in snapshots],
        "avg_complexity": [s.avg_complexity for s in snapshots],
        "complex_functions": [s.complex_functions for s in snapshots],
        "mi": [s.avg_maintainability for s in snapshots],
        "duplication": [round(s.duplication_ratio * 100, 1) for s in snapshots],
        "security_issues": [s.security_issues for s in snapshots],
        "dependency_count": [s.dependency_count for s in snapshots],
        "unpinned": [s.unpinned_dependencies for s in snapshots],
        "todo": [s.todo_count for s in snapshots],
    }


def _language_bars(snapshot):
    total = sum(snapshot.languages.values()) or 1
    return [
        {"name": name, "lines": lines, "share": lines / total * 100, "color": LANGUAGE_COLORS.get(name, "#8a8577")}
        for name, lines in snapshot.languages.items()
    ]


def _module_bars(snapshot):
    total = sum(snapshot.modules.values()) or 1
    return [{"name": n, "lines": l, "share": l / total * 100} for n, l in snapshot.modules.items()]


def _repo_context(repo, active):
    snapshots = list(repo.snapshots.all())
    return {
        "repo": repo,
        "snapshots": snapshots,
        "latest": snapshots[-1] if snapshots else None,
        "previous": snapshots[-2] if len(snapshots) > 1 else None,
        "run": repo.runs.first(),
        "active_tab": active,
    }


# ---------------------------------------------------------------- pages

def home(request):
    form = RepositoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.parsed
        repo = Repository.objects.filter(url=data["url"]).first() or Repository.objects.filter(
            owner__iexact=data["owner"], name__iexact=data["name"], source=data["source"]
        ).first()
        if repo is None:
            repo = Repository.objects.create(**data)
        if not repo.snapshots.exists() or not repo.active_run:
            start_analysis(repo)
        return redirect(repo)

    repos = list(Repository.objects.annotate(n_snapshots=Count("snapshots")))
    cards = []
    for repo in repos:
        snaps = list(repo.snapshots.all())
        cards.append({
            "repo": repo,
            "latest": snaps[-1] if snaps else None,
            "first": snaps[0] if snaps else None,
            "strand": _strand(snaps[-10:]),
            "spark": [s.health_score for s in snaps],
            "run": repo.runs.first(),
        })
    totals = {
        "repos": len(repos),
        "snapshots": Snapshot.objects.count(),
        "findings": Finding.objects.exclude(severity="good").count(),
    }
    return render(request, "dna/home.html", {"form": form, "cards": cards, "examples": EXAMPLES, "totals": totals})


def repo_overview(request, slug):
    repo = _repo(slug)
    ctx = _repo_context(repo, "overview")
    latest, previous = ctx["latest"], ctx["previous"]
    if latest:
        findings = list(latest.findings.exclude(severity="good")[:5])
        wins = list(latest.findings.filter(severity="good")[:2])
        ctx.update({
            "cards": _metric_cards(latest, previous),
            "strand": _strand(ctx["snapshots"]),
            "series": _chart_series(ctx["snapshots"]),
            "fix_first": findings,
            "wins": wins,
            "hotspots": latest.files.filter(is_test=False, hotspot_score__gt=0).order_by("-hotspot_score")[:6],
            "language_bars": _language_bars(latest),
            "module_bars": _module_bars(latest),
            "severity_counts": Counter(latest.findings.values_list("severity", flat=True)),
            "health_delta": latest.health_score - previous.health_score if previous else None,
        })
    return render(request, "dna/repo_overview.html", ctx)


def repo_timeline(request, slug):
    repo = _repo(slug)
    ctx = _repo_context(repo, "timeline")
    if not ctx["latest"]:
        return redirect(repo)
    rows = []
    prev = None
    for s in ctx["snapshots"]:
        rows.append({"s": s, "delta": None if prev is None else s.health_score - prev.health_score,
                     "regressions": s.findings.filter(is_regression=True).count()})
        prev = s
    ctx.update({"series": _chart_series(ctx["snapshots"]), "rows": list(reversed(rows))})
    return render(request, "dna/repo_timeline.html", ctx)


def repo_hotspots(request, slug):
    repo = _repo(slug)
    ctx = _repo_context(repo, "hotspots")
    latest = ctx["latest"]
    if not latest:
        return redirect(repo)
    files = list(latest.files.filter(is_test=False).order_by("-hotspot_score")[:200])
    points = [
        {"x": f.churn, "y": f.complexity, "r": f.code_lines, "path": f.path, "score": f.hotspot_score}
        for f in files if f.complexity > 0
    ]
    ctx.update({"files": files[:40], "points": points, "functions": latest.hot_functions})
    return render(request, "dna/repo_hotspots.html", ctx)


def repo_findings(request, slug):
    repo = _repo(slug)
    ctx = _repo_context(repo, "findings")
    snapshot = ctx["latest"]
    if not snapshot:
        return redirect(repo)
    sha = request.GET.get("at")
    if sha:
        snapshot = _snapshot(repo, sha)
    qs = snapshot.findings.all()
    category = request.GET.get("category", "")
    severity = request.GET.get("severity", "")
    only_regressions = request.GET.get("regressions") == "1"
    if category:
        qs = qs.filter(category=category)
    if severity:
        qs = qs.filter(severity=severity)
    if only_regressions:
        qs = qs.filter(is_regression=True)
    counts = snapshot.findings.values("category").annotate(n=Count("id"))
    ctx.update({
        "snapshot": snapshot,
        "findings": qs,
        "category": category,
        "severity": severity,
        "only_regressions": only_regressions,
        "categories": [(c, l, next((x["n"] for x in counts if x["category"] == c), 0)) for c, l in Finding.Category.choices],
        "severities": Finding.Severity.choices,
    })
    return render(request, "dna/repo_findings.html", ctx)


def repo_dependencies(request, slug):
    repo = _repo(slug)
    ctx = _repo_context(repo, "dependencies")
    latest = ctx["latest"]
    if not latest:
        return redirect(repo)
    deps = latest.dependencies
    ctx.update({
        "deps": deps,
        "pin_counts": Counter(d["pin"] for d in deps),
        "risky": [d for d in deps if d.get("risk")],
        "series": _chart_series(ctx["snapshots"]),
    })
    return render(request, "dna/repo_dependencies.html", ctx)


def repo_compare(request, slug):
    repo = _repo(slug)
    ctx = _repo_context(repo, "compare")
    snapshots = ctx["snapshots"]
    if len(snapshots) < 2:
        messages.info(request, "Comparing needs at least two snapshots.")
        return redirect(repo)
    a = _snapshot(repo, request.GET["a"]) if request.GET.get("a") else snapshots[0]
    b = _snapshot(repo, request.GET["b"]) if request.GET.get("b") else snapshots[-1]
    if a.committed_at > b.committed_at:
        a, b = b, a

    score_rows = [
        {"label": label, "a": getattr(a, field), "b": getattr(b, field), "delta": getattr(b, field) - getattr(a, field)}
        for field, label in [("health_score", "Overall health")] + Snapshot.SCORE_FIELDS
    ]
    metric_rows = []
    for card in _metric_cards(b, a):
        card["old"] = getattr(a, card["field"])
        metric_rows.append(card)

    files_a = {f.path: f for f in a.files.filter(is_test=False)}
    files_b = {f.path: f for f in b.files.filter(is_test=False)}
    changed = []
    for path, fb in files_b.items():
        fa = files_a.get(path)
        if fa:
            d = fb.complexity - fa.complexity
            if d:
                changed.append({"path": path, "before": fa.complexity, "after": fb.complexity, "delta": d,
                                "lines_before": fa.code_lines, "lines_after": fb.code_lines})
    worse = sorted([c for c in changed if c["delta"] > 0], key=lambda c: -c["delta"])[:10]
    better = sorted([c for c in changed if c["delta"] < 0], key=lambda c: c["delta"])[:10]
    added = sorted(set(files_b) - set(files_a))
    removed = sorted(set(files_a) - set(files_b))

    titles_a = set(a.findings.values_list("title", flat=True))
    new_findings = [f for f in b.findings.exclude(severity="good") if f.title not in titles_a]
    titles_b = set(b.findings.values_list("title", flat=True))
    resolved = [f for f in a.findings.exclude(severity="good") if f.title not in titles_b]

    ctx.update({
        "a": a, "b": b, "score_rows": score_rows, "metric_rows": metric_rows,
        "worse": worse, "better": better, "added": added, "removed": removed,
        "added_count": len(added), "removed_count": len(removed),
        "new_findings": new_findings[:12], "resolved": resolved[:12],
        "days": (b.committed_at - a.committed_at).days,
        "strand": _strand([a, b]),
    })
    return render(request, "dna/repo_compare.html", ctx)


def snapshot_detail(request, slug, sha):
    repo = _repo(slug)
    ctx = _repo_context(repo, "timeline")
    snapshot = _snapshot(repo, sha)
    snaps = ctx["snapshots"]
    idx = snaps.index(snapshot)
    previous = snaps[idx - 1] if idx > 0 else None
    sort = request.GET.get("sort", "-hotspot_score")
    allowed = {"path", "-code_lines", "-complexity", "maintainability", "-churn", "-hotspot_score", "-max_function_complexity"}
    sort = sort if sort in allowed else "-hotspot_score"
    query = request.GET.get("q", "").strip()
    files = snapshot.files.all()
    if query:
        files = files.filter(path__icontains=query)
    ctx.update({
        "snapshot": snapshot,
        "prev_snapshot": previous,
        "next_snapshot": snaps[idx + 1] if idx + 1 < len(snaps) else None,
        "cards": _metric_cards(snapshot, previous),
        "findings": snapshot.findings.all()[:12],
        "files": files.order_by(sort, "path")[:150],
        "file_total": files.count(),
        "sort": sort,
        "query": query,
        "language_bars": _language_bars(snapshot),
    })
    return render(request, "dna/snapshot_detail.html", ctx)


def how_it_works(request):
    return render(request, "dna/how_it_works.html", {"active_page": "how"})


# ---------------------------------------------------------------- actions & API

@require_POST
def reanalyze(request, slug):
    repo = _repo(slug)
    full = request.POST.get("mode") == "full"
    start_analysis(repo, reanalyze=full)
    messages.success(request, "Re-analysis started. This page updates by itself." if full
                     else "Looking for new commits since the last analysis…")
    return redirect(repo)


@require_POST
def delete_repo(request, slug):
    repo = _repo(slug)
    name = repo.full_name
    repo.delete()
    messages.success(request, f"{name} and all of its snapshots were removed.")
    return redirect("dna:home")


def run_status(request, slug):
    repo = _repo(slug)
    run = repo.runs.first()
    if not run:
        return JsonResponse({"status": "none"})
    return JsonResponse({
        "status": run.status,
        "stage": run.stage,
        "progress": run.progress,
        "error": run.error,
        "snapshots": repo.snapshots.count(),
        "url": reverse("dna:repo", args=[repo.slug]),
    })


def api_repo(request, slug):
    """A small JSON view of the DNA profile, handy for scripts and badges."""
    repo = _repo(slug)
    snapshots = repo.snapshots.all()
    return JsonResponse({
        "repository": repo.full_name,
        "snapshots": [
            {
                "commit": s.commit_sha, "label": s.label, "date": s.committed_at.isoformat(),
                "health": s.health_score, "grade": s.grade,
                "scores": {label.lower(): score for label, score, _ in s.scores()},
                "code_lines": s.code_lines, "avg_complexity": s.avg_complexity,
                "test_ratio": s.test_ratio, "duplication": s.duplication_ratio,
                "security_issues": s.security_issues, "dependencies": s.dependency_count,
            }
            for s in snapshots
        ],
    })
