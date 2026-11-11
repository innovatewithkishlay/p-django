"""
Explain a snapshot in words.

The rules here compare the current snapshot with the one before it (and
with the very first one, for long-term drift) and produce findings: what
happened, why it matters, and what to do first. Each finding gets a
priority so the "fix this first" list is ordered by impact, not by rule.
"""

from dataclasses import dataclass, field

from .source import COMPLEX_FUNCTION_CC

SEVERITY_BASE = {"critical": 0, "high": 20, "medium": 45, "low": 70, "good": 90}


@dataclass
class Insight:
    category: str
    severity: str
    title: str
    explanation: str
    recommendation: str = ""
    file_path: str = ""
    line: int = None
    priority: int = 50
    is_regression: bool = False


@dataclass
class Context:
    snapshot: object
    previous: object = None
    first: object = None
    files: dict = field(default_factory=dict)  # path -> FileMetric
    previous_files: dict = field(default_factory=dict)
    security_hits: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)


def pct(new, old):
    if not old:
        return None
    return (new - old) / old * 100


def _fmt_pct(value):
    return f"{abs(value):.0f}%"


def _insight(severity, category, title, explanation, recommendation="", bump=0, **kw):
    return Insight(category, severity, title, explanation, recommendation,
                   priority=max(0, min(99, SEVERITY_BASE[severity] + bump)), **kw)


def generate(ctx):
    rules = [
        _health_change, _complexity_drift, _test_drift, _security, _hotspots,
        _complex_functions, _large_files, _duplication, _dependencies,
        _documentation, _architecture, _todos,
    ]
    insights = []
    for rule in rules:
        insights.extend(rule(ctx) or [])
    insights.sort(key=lambda i: (i.priority, i.title))
    return insights


# ------------------------------------------------------------------ rules

def _health_change(ctx):
    s, p = ctx.snapshot, ctx.previous
    if not p:
        return []
    delta = s.health_score - p.health_score
    if abs(delta) < 4:
        return []
    moves = []
    for field_name, label in s.SCORE_FIELDS:
        d = getattr(s, field_name) - getattr(p, field_name)
        if abs(d) >= 3:
            moves.append((d, label))
    moves.sort()
    if delta < 0:
        drivers = ", ".join(f"{label.lower()} ({d:+d})" for d, label in moves if d < 0) or "several small changes"
        return [_insight(
            "high" if delta <= -10 else "medium", "maintainability",
            f"Health dropped {abs(delta)} points since {p.label}",
            f"The overall score went from {p.health_score} to {s.health_score}. Most of the drop comes from {drivers}.",
            "Start with the regressions listed below; they are the changes that moved the score.",
            bump=-10, is_regression=True,
        )]
    drivers = ", ".join(f"{label.lower()} ({d:+d})" for d, label in reversed(moves) if d > 0) or "steady small fixes"
    return [_insight(
        "good", "maintainability", f"Health improved {delta} points since {p.label}",
        f"The score rose from {p.health_score} to {s.health_score}, mainly thanks to {drivers}. Whatever the team did here is worth keeping.",
    )]


def _complexity_drift(ctx):
    s, p = ctx.snapshot, ctx.previous
    out = []
    if p and p.avg_complexity:
        change = pct(s.avg_complexity, p.avg_complexity)
        if change is not None and change >= 12:
            growers = []
            for path, fm in ctx.files.items():
                old = ctx.previous_files.get(path)
                if fm.is_test:
                    continue
                before = old.complexity if old else 0
                if fm.complexity - before >= 5:
                    growers.append((fm.complexity - before, path))
            growers.sort(reverse=True)
            names = ", ".join(f"{path} (+{d})" for d, path in growers[:3])
            out.append(_insight(
                "high" if change >= 25 else "medium", "complexity",
                f"Average function complexity rose {_fmt_pct(change)}",
                f"A typical function now has {s.avg_complexity:.1f} decision points, up from {p.avg_complexity:.1f} at {p.label}. "
                + (f"The biggest increases are in {names}." if names else "The increase is spread across many files."),
                "Extract the branchy parts into small, named helpers and replace long if/else chains with lookups or polymorphism.",
                file_path=growers[0][1] if growers else "", is_regression=True,
            ))
        elif change is not None and change <= -12:
            out.append(_insight(
                "good", "complexity", f"Functions got simpler ({_fmt_pct(change)} less complex on average)",
                f"Average complexity fell from {p.avg_complexity:.1f} to {s.avg_complexity:.1f}. Smaller functions are easier to test and review.",
            ))
    first = ctx.first
    if first and first.pk != getattr(p, "pk", None) and first.avg_complexity and s.avg_complexity > first.avg_complexity * 1.4:
        out.append(_insight(
            "medium", "complexity", "Complexity has been creeping up for a while",
            f"Since the first snapshot ({first.label}) average complexity went from {first.avg_complexity:.1f} to "
            f"{s.avg_complexity:.1f}. No single change caused it: this is slow drift, the most common way code becomes hard to change.",
            "Agree on a limit (for example: new functions stay under 10) and enforce it in review or with a linter rule.",
            bump=5,
        ))
    return out


def _test_drift(ctx):
    s, p = ctx.snapshot, ctx.previous
    source_lines = s.code_lines - s.test_lines
    if s.test_files == 0 and source_lines > 300:
        return [_insight(
            "critical" if source_lines > 3000 else "high", "testing", "There are no automated tests",
            f"{source_lines:,} lines of code and not a single test file was found. Every change is checked by hand, "
            "so refactoring — the main cure for everything else on this page — is risky.",
            "Start with the hotspots: write a few tests around the most-changed files before touching them.",
            bump=-5,
        )]
    out = []
    if p:
        prev_source = p.code_lines - p.test_lines
        code_growth = pct(source_lines, prev_source)
        test_growth = pct(s.test_lines, p.test_lines) if p.test_lines else None
        if code_growth is not None and code_growth > 10 and (test_growth is None or test_growth < code_growth / 3):
            out.append(_insight(
                "high" if s.test_ratio < 0.2 else "medium", "testing", "Code is growing faster than its tests",
                f"Source code grew {_fmt_pct(code_growth)} since {p.label}, but tests grew "
                f"{_fmt_pct(test_growth or 0)}. The share of code covered by tests is shrinking with every change.",
                "Make 'comes with a test' part of the definition of done for new features.",
                is_regression=True,
            ))
        elif p.test_ratio and s.test_ratio > p.test_ratio * 1.15 and s.test_lines - p.test_lines > 50:
            out.append(_insight(
                "good", "testing", "Test suite is catching up",
                f"Tests grew from {p.test_lines:,} to {s.test_lines:,} lines; the test-to-code ratio is now {s.test_ratio:.2f}.",
            ))
    if s.test_files and s.test_ratio < 0.15 and not out:
        out.append(_insight(
            "medium", "testing", "Tests are thin compared to the code",
            f"There are {s.test_lines:,} lines of tests for {source_lines:,} lines of code (ratio {s.test_ratio:.2f}). "
            "Healthy projects usually sit between 0.3 and 1.0.",
            "Add tests to the hotspot files first — that is where bugs are most likely to land.",
        ))
    if not s.has_ci:
        out.append(_insight(
            "low", "testing", "No continuous integration found",
            "No CI configuration (GitHub Actions, GitLab CI, CircleCI…) was found, so tests only run when someone remembers to.",
            "Add a small workflow that runs the test suite on every pull request.",
        ))
    return out


def _security(ctx):
    out = []
    by_rule = {}
    for hit in ctx.security_hits:
        by_rule.setdefault(hit.rule.id, []).append(hit)
    for rule_id, hits in by_rule.items():
        rule = hits[0].rule
        if rule.severity in {"critical", "high"}:
            for hit in hits[:5]:
                out.append(_insight(
                    rule.severity, "security", f"{rule.title}",
                    f"{rule.explanation}\n\nFound in {hit.path} line {hit.line}: `{hit.snippet}`",
                    rule.recommendation, file_path=hit.path, line=hit.line, bump=-5,
                ))
            if len(hits) > 5:
                out.append(_insight(
                    rule.severity, "security", f"{rule.title} ({len(hits) - 5} more places)",
                    f"The same pattern appears in {len(hits) - 5} more places.", rule.recommendation, bump=5,
                ))
        else:
            places = ", ".join(f"{h.path}:{h.line}" for h in hits[:4])
            more = f" and {len(hits) - 4} more" if len(hits) > 4 else ""
            out.append(_insight(
                rule.severity, "security", f"{rule.title} ({len(hits)}×)" if len(hits) > 1 else rule.title,
                f"{rule.explanation}\n\nSeen at {places}{more}.", rule.recommendation,
                file_path=hits[0].path, line=hits[0].line,
            ))
    p = ctx.previous
    if p and ctx.snapshot.security_issues > p.security_issues:
        new = ctx.snapshot.security_issues - p.security_issues
        out.append(_insight(
            "medium", "security", f"{new} new risky pattern{'s' if new > 1 else ''} since {p.label}",
            "These were introduced between the two snapshots, which usually means they slipped through review.",
            "Add a security linter (bandit, semgrep, eslint-plugin-security) to CI so this is caught automatically.",
            is_regression=True, bump=-8,
        ))
    return out


def _hotspots(ctx):
    candidates = [fm for fm in ctx.files.values() if not fm.is_test and fm.hotspot_score >= 35 and fm.churn >= 3]
    candidates.sort(key=lambda f: -f.hotspot_score)
    out = []
    for rank, fm in enumerate(candidates[:3]):
        out.append(_insight(
            "high" if fm.hotspot_score >= 70 else "medium", "hotspot", f"Hotspot: {fm.path}",
            f"Changed in {fm.churn} recent commits by {fm.authors} {'person' if fm.authors == 1 else 'people'}, with a total "
            f"complexity of {fm.complexity}"
            + (f" (its busiest function, {fm.max_function_name}, scores {fm.max_function_complexity})" if fm.max_function_name else "")
            + ". Files that are both complicated and constantly edited are where most bugs and slow reviews come from.",
            "This is the best place to spend refactoring time: split it by responsibility and cover it with tests first.",
            file_path=fm.path, bump=rank * 3 - 6,
        ))
    return out


def _complex_functions(ctx):
    out = []
    worst = [f for f in ctx.snapshot.hot_functions if f["complexity"] > 20][:3]
    for fn in worst:
        out.append(_insight(
            "high" if fn["complexity"] > 35 else "medium", "complexity",
            f"{fn['name']}() is very hard to follow",
            f"It has a cyclomatic complexity of {fn['complexity']} across {fn['length']} lines. "
            f"Above {COMPLEX_FUNCTION_CC} people start missing cases; above 20 it needs at least that many tests to cover every path.",
            "Split it at the natural seams (validation, branching on type, the actual work) and give each part a name.",
            file_path=fn["path"], line=fn["line"], bump=-(fn["complexity"] // 10),
        ))
    s = ctx.snapshot
    if s.function_count and s.complex_functions / s.function_count > 0.12:
        out.append(_insight(
            "medium", "complexity", f"{s.complex_functions} functions are above the complexity limit",
            f"That is {s.complex_functions / s.function_count:.0%} of all functions over a cyclomatic complexity of {COMPLEX_FUNCTION_CC}.",
            "Track this number over time; aim to bring it down a little with every change to those files.",
        ))
    return out


def _large_files(ctx):
    big = sorted((fm for fm in ctx.files.values() if not fm.is_test and fm.code_lines > 800), key=lambda f: -f.code_lines)
    if not big:
        return []
    names = ", ".join(f"{fm.path} ({fm.code_lines:,} lines)" for fm in big[:3])
    return [_insight(
        "medium" if big[0].code_lines < 2000 else "high", "maintainability",
        f"{len(big)} file{'s are' if len(big) > 1 else ' is'} very large",
        f"Large files tend to collect unrelated responsibilities and attract merge conflicts. Largest: {names}.",
        "Look for groups of functions that only talk to each other and move them into their own module.",
        file_path=big[0].path,
    )]


def _duplication(ctx):
    s, p = ctx.snapshot, ctx.previous
    if s.duplication_ratio < 0.08:
        return []
    trend = ""
    if p and s.duplication_ratio > p.duplication_ratio + 0.02:
        trend = f" It was {p.duplication_ratio:.0%} at {p.label}."
    return [_insight(
        "high" if s.duplication_ratio > 0.2 else "medium", "maintainability",
        f"{s.duplication_ratio:.0%} of the code is copy-pasted",
        "Blocks of six or more identical lines appear in several places. A bug fixed in one copy stays alive in the others." + trend,
        "Pull the shared block into a function; if the copies differ slightly, pass the difference in as a parameter.",
        is_regression=bool(trend),
    )]


def _dependencies(ctx):
    out = []
    s = ctx.snapshot
    risky = [d for d in ctx.dependencies if d.get("risk")]
    for d in risky[:4]:
        out.append(_insight(
            "medium", "dependencies", f"Dependency to replace: {d['name']}",
            f"{d['risk']} (declared in {d['manifest']} as {d['spec']}).",
            f"Plan a replacement for {d['name']}; abandoned packages stop receiving security fixes.",
            file_path=d["manifest"],
        ))
    if s.dependency_count and s.unpinned_dependencies / s.dependency_count > 0.25:
        names = ", ".join(d["name"] for d in ctx.dependencies if d["pin"] == "unpinned")[:200]
        out.append(_insight(
            "medium", "dependencies", f"{s.unpinned_dependencies} dependencies have no version limit",
            f"Unpinned packages ({names}) install whatever version is newest, so a release upstream can break the build overnight "
            "and two machines can run different code.",
            "Pin exact versions in a lock file (pip-tools, poetry.lock, package-lock.json) and update them deliberately.",
        ))
    p = ctx.previous
    if p and p.dependency_count and s.dependency_count > p.dependency_count * 1.3 and s.dependency_count - p.dependency_count >= 5:
        out.append(_insight(
            "low", "dependencies", f"Dependencies grew from {p.dependency_count} to {s.dependency_count}",
            "Every new package is code you did not write but still have to keep updated and secure.",
            "Review the new additions and drop any that are only used in one small place.",
            is_regression=True,
        ))
    return out


def _documentation(ctx):
    s = ctx.snapshot
    out = []
    if not s.has_readme:
        out.append(_insight(
            "medium", "documentation", "No README",
            "New contributors have nowhere to start: how to install, run and test the project is tribal knowledge.",
            "Add a README with what the project does, how to run it locally, and how to run the tests.",
        ))
    if not s.has_license:
        out.append(_insight(
            "low", "documentation", "No license file",
            "Without a license, nobody else is legally allowed to use or modify the code, even if it is public.",
            "Pick a license (choosealicense.com helps) and add it as LICENSE at the root.",
        ))
    if s.code_lines > 2000 and s.comment_ratio < 0.04:
        out.append(_insight(
            "low", "documentation", "Very few comments or docstrings",
            f"Only {s.comment_ratio:.1%} of lines are comments. Code shows what happens; comments are where the why lives.",
            "You don't need more comments everywhere — just docstrings on public functions and a note wherever a decision was not obvious.",
        ))
    return out


def _architecture(ctx):
    s = ctx.snapshot
    mods = s.modules or {}
    total = sum(mods.values())
    out = []
    if total > 3000 and mods:
        name, size = max(mods.items(), key=lambda kv: kv[1])
        share = size / total
        if share > 0.6 and len(mods) > 1 and name != "(root)":
            out.append(_insight(
                "low", "architecture", f"Most of the code lives in {name}/",
                f"{share:.0%} of all source lines are in one top-level folder. That is often a sign of a 'god module' "
                "that everything depends on.",
                "Map out the main responsibilities inside it and give the clearest ones their own package.",
            ))
    p = ctx.previous
    if p and p.modules:
        new_modules = set(mods) - set(p.modules)
        big_new = [m for m in new_modules if mods.get(m, 0) > 300]
        if big_new:
            out.append(_insight(
                "low", "architecture", f"New area{'s' if len(big_new) > 1 else ''} of the codebase: {', '.join(sorted(big_new))}",
                "These top-level folders did not exist in the previous snapshot.",
                "Make sure the README explains what lives where.",
            ))
    return out


def _todos(ctx):
    s, p = ctx.snapshot, ctx.previous
    if s.todo_count < 15:
        return []
    trend = f", up from {p.todo_count}" if p and s.todo_count > p.todo_count + 5 else ""
    return [_insight(
        "low", "maintainability", f"{s.todo_count} TODO / FIXME notes{trend}",
        "Each TODO is a small promise that was not kept. They pile up quietly and become invisible.",
        "Turn the important ones into issues and delete the rest.",
        is_regression=bool(trend),
    )]


# ------------------------------------------------------------------ summary

def summarize(ctx):
    s, p, first = ctx.snapshot, ctx.previous, ctx.first
    langs = list((s.languages or {}).items())
    main = langs[0][0] if langs else "code"
    parts = [
        f"At {s.label} the project has {s.code_lines:,} lines of code in {s.total_files:,} files, mostly {main}. "
        f"Its health score is {s.health_score}/100 (grade {s.grade})."
    ]
    if p:
        growth = pct(s.code_lines, p.code_lines)
        delta = s.health_score - p.health_score
        if growth is not None:
            direction = "grew" if growth >= 0 else "shrank"
            parts.append(f"Since {p.label} the code {direction} {_fmt_pct(growth)} and the score moved {delta:+d}.")
    if first and p and first.pk != p.pk:
        parts.append(f"Over the whole tracked history the score went from {first.health_score} to {s.health_score}.")
    weakest = min(s.scores(), key=lambda t: t[1])
    strongest = max(s.scores(), key=lambda t: t[1])
    parts.append(f"Strongest area: {strongest[0].lower()} ({strongest[1]}). Weakest: {weakest[0].lower()} ({weakest[1]}).")
    return " ".join(parts)
