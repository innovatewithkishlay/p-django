"""
Walk a checked-out tree and turn it into one set of measurements.

The result (``TreeMetrics``) is plain data: it knows nothing about Django.
The pipeline turns it into model rows.
"""

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import dependencies, languages, security, source

DUP_WINDOW = 6


@dataclass
class TreeMetrics:
    files: list = field(default_factory=list)  # FileReport
    test_paths: set = field(default_factory=set)
    security_hits: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    duplication_ratio: float = 0.0
    duplicated_lines: dict = field(default_factory=dict)  # path -> count
    has_readme: bool = False
    has_license: bool = False
    has_ci: bool = False
    readme_lines: int = 0

    # ---- convenient aggregates
    @property
    def source_files(self):
        return [f for f in self.files if f.path not in self.test_paths]

    @property
    def test_files(self):
        return [f for f in self.files if f.path in self.test_paths]

    @property
    def all_functions(self):
        return [(f.path, fn) for f in self.source_files for fn in f.functions]

    def languages(self):
        counts = Counter()
        for f in self.files:
            counts[f.language] += f.code_lines
        return dict(counts.most_common())

    def modules(self):
        counts = Counter()
        for f in self.source_files:
            counts[languages.top_level_module(f.path)] += f.code_lines
        return dict(counts.most_common(12))


def _read(path, max_bytes):
    try:
        if path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def collect(root, paths, max_bytes=400_000):
    root = Path(root)
    metrics = TreeMetrics()
    lowered = [p.lower() for p in paths]
    metrics.has_readme = any(p.split("/")[-1].startswith("readme") and p.count("/") == 0 for p in lowered)
    metrics.has_license = any(p.split("/")[-1].startswith(("license", "licence", "copying")) and p.count("/") == 0 for p in lowered)
    metrics.has_ci = any(p.startswith(marker.lower()) for p in lowered for marker in languages.CI_MARKERS)

    raw_deps = []
    for rel in paths:
        if languages.is_ignored(rel):
            continue
        full = root / rel
        if rel.lower().split("/")[-1].startswith("readme") and rel.count("/") == 0:
            text = _read(full, max_bytes)
            metrics.readme_lines = len((text or "").splitlines())
        parser = dependencies.parser_for(rel)
        if parser:
            text = _read(full, max_bytes)
            if text:
                raw_deps.extend(parser(text, rel))
            continue
        entry = languages.language_for(rel)
        if not entry:
            continue
        text = _read(full, max_bytes)
        if text is None or not text.strip():
            continue
        report = source.analyze_file(rel, text, entry)
        is_test = languages.is_test_path(rel)
        if is_test:
            metrics.test_paths.add(rel)
        metrics.files.append(report)
        metrics.security_hits.extend(security.scan(rel, text, report.language, is_test))

    metrics.dependencies = dependencies.summarize(raw_deps)
    _measure_duplication(metrics)
    return metrics


def _measure_duplication(metrics):
    windows = defaultdict(list)
    for f in metrics.source_files:
        lines = f.normalized_lines
        for i in range(len(lines) - DUP_WINDOW + 1):
            chunk = "\n".join(text for _, text in lines[i:i + DUP_WINDOW])
            digest = hashlib.blake2b(chunk.encode(), digest_size=8).digest()
            windows[digest].append((f.path, i))

    covered = defaultdict(set)
    for spots in windows.values():
        if len(spots) < 2:
            continue
        for path, i in spots:
            covered[path].update(range(i, i + DUP_WINDOW))

    total = sum(len(f.normalized_lines) for f in metrics.source_files)
    duplicated = sum(len(v) for v in covered.values())
    metrics.duplicated_lines = {path: len(v) for path, v in covered.items()}
    metrics.duplication_ratio = round(duplicated / total, 4) if total else 0.0
    # Free memory: the normalised text is no longer needed.
    for f in metrics.files:
        f.normalized_lines = []
