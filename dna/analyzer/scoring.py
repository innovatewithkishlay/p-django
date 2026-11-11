"""
Turn raw measurements into 0-100 scores.

Every score is a blend of a few signals, each mapped linearly between a
"good" value (100) and a "bad" value (0). The thresholds come from common
industry rules of thumb (McCabe's 10, SEI maintainability bands, etc.) and
are kept in one place so they are easy to argue with.
"""

from dataclasses import dataclass

WEIGHTS = {
    "complexity": 0.20,
    "maintainability": 0.20,
    "testing": 0.20,
    "security": 0.20,
    "dependencies": 0.10,
    "documentation": 0.10,
}
SEVERITY_WEIGHT = {"critical": 25, "high": 10, "medium": 4, "low": 1}


def scale(value, good, bad):
    """100 at ``good``, 0 at ``bad``, linear in between (works either direction)."""
    if good == bad:
        return 100.0
    t = (value - good) / (bad - good)
    return max(0.0, min(100.0, 100.0 * (1 - t)))


@dataclass
class Scores:
    complexity: int
    maintainability: int
    testing: int
    security: int
    dependencies: int
    documentation: int

    @property
    def health(self):
        total = sum(getattr(self, k) * w for k, w in WEIGHTS.items())
        return int(round(total))


def compute(m):
    """``m`` is a dict of snapshot-level numbers (see pipeline.aggregate)."""
    functions = max(m["function_count"], 1)
    complex_share = m["complex_functions"] / functions
    long_share = m["long_functions"] / functions
    source_files = max(m["source_files"], 1)
    large_share = m["large_files"] / source_files

    complexity = (
        0.5 * scale(m["avg_complexity"], 2.0, 10.0)
        + 0.3 * scale(complex_share, 0.02, 0.25)
        + 0.2 * scale(m["max_nesting"], 3, 9)
    )

    maintainability = (
        0.45 * scale(m["avg_maintainability"], 65, 20)
        + 0.25 * scale(m["duplication_ratio"], 0.03, 0.25)
        + 0.15 * scale(large_share, 0.0, 0.2)
        + 0.15 * scale(long_share, 0.02, 0.2)
    )

    if m["test_files"] == 0:
        testing = 10.0 if m["has_ci"] else 0.0
    else:
        testing = 0.8 * scale(m["test_ratio"], 0.7, 0.0) + 0.2 * (100 if m["has_ci"] else 30)

    penalty = sum(SEVERITY_WEIGHT[s] * n for s, n in m["security_by_severity"].items())
    kloc = max(m["code_lines"] / 1000, 1)
    security = scale(penalty / kloc, 0, 20)
    if m["security_by_severity"].get("critical"):
        security = min(security, 45)
    elif m["security_by_severity"].get("high"):
        security = min(security, 75)

    deps = m["dependency_count"]
    if deps == 0:
        dependencies = 90.0
    else:
        unpinned_share = m["unpinned_dependencies"] / deps
        dependencies = (
            0.55 * scale(unpinned_share, 0.0, 0.6)
            + 0.30 * scale(m["risky_dependencies"], 0, 4)
            + 0.15 * scale(deps, 30, 250)
        )

    documentation = (
        (40 if m["has_readme"] else 0) * (1.0 if m["readme_lines"] >= 20 else 0.6)
        + 0.45 * scale(m["comment_ratio"], 0.18, 0.02)
        + (15 if m["has_license"] else 0)
    )

    return Scores(*(int(round(v)) for v in (complexity, maintainability, testing, security, dependencies, documentation)))


def hotspot(churn, complexity, max_churn, max_complexity):
    """A file is a hotspot when it is both complicated and changed often."""
    if not churn or not complexity or not max_churn or not max_complexity:
        return 0.0
    return round(100 * (churn / max_churn) ** 0.6 * (complexity / max_complexity) ** 0.4, 1)
