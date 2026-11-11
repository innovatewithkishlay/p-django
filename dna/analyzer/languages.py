"""Which files we look at, what language they are, and whether they are tests."""

import re
from pathlib import PurePosixPath

# extension -> (language, line comment tokens, (block open, block close) or None, style)
# style: "python" uses the ast module, "brace" uses brace matching, "indent" is
# best-effort keyword counting for languages we do not parse deeply.
LANGUAGES = {
    ".py": ("Python", ("#",), None, "python"),
    ".js": ("JavaScript", ("//",), ("/*", "*/"), "brace"),
    ".jsx": ("JavaScript", ("//",), ("/*", "*/"), "brace"),
    ".mjs": ("JavaScript", ("//",), ("/*", "*/"), "brace"),
    ".cjs": ("JavaScript", ("//",), ("/*", "*/"), "brace"),
    ".ts": ("TypeScript", ("//",), ("/*", "*/"), "brace"),
    ".tsx": ("TypeScript", ("//",), ("/*", "*/"), "brace"),
    ".java": ("Java", ("//",), ("/*", "*/"), "brace"),
    ".kt": ("Kotlin", ("//",), ("/*", "*/"), "brace"),
    ".go": ("Go", ("//",), ("/*", "*/"), "brace"),
    ".rs": ("Rust", ("//",), ("/*", "*/"), "brace"),
    ".c": ("C", ("//",), ("/*", "*/"), "brace"),
    ".h": ("C", ("//",), ("/*", "*/"), "brace"),
    ".cpp": ("C++", ("//",), ("/*", "*/"), "brace"),
    ".cc": ("C++", ("//",), ("/*", "*/"), "brace"),
    ".hpp": ("C++", ("//",), ("/*", "*/"), "brace"),
    ".cs": ("C#", ("//",), ("/*", "*/"), "brace"),
    ".php": ("PHP", ("//", "#"), ("/*", "*/"), "brace"),
    ".swift": ("Swift", ("//",), ("/*", "*/"), "brace"),
    ".scala": ("Scala", ("//",), ("/*", "*/"), "brace"),
    ".dart": ("Dart", ("//",), ("/*", "*/"), "brace"),
    ".rb": ("Ruby", ("#",), ("=begin", "=end"), "indent"),
    ".sh": ("Shell", ("#",), None, "indent"),
    ".lua": ("Lua", ("--",), ("--[[", "]]"), "indent"),
    ".ex": ("Elixir", ("#",), None, "indent"),
    ".exs": ("Elixir", ("#",), None, "indent"),
    ".vue": ("Vue", ("//",), ("/*", "*/"), "brace"),
    ".svelte": ("Svelte", ("//",), ("/*", "*/"), "brace"),
}

# Colours used across the UI for each language (kept muted on purpose).
LANGUAGE_COLORS = {
    "Python": "#3b6ea5",
    "JavaScript": "#c9a227",
    "TypeScript": "#2f74c0",
    "Java": "#b0722a",
    "Kotlin": "#7f52ff",
    "Go": "#2a9bb5",
    "Rust": "#a3532b",
    "C": "#6b7b8c",
    "C++": "#a2457a",
    "C#": "#5a8f3a",
    "PHP": "#6c78ad",
    "Swift": "#e0623a",
    "Scala": "#c2403c",
    "Dart": "#2b8fa8",
    "Ruby": "#a8302a",
    "Shell": "#5d8a4b",
    "Lua": "#2c3e91",
    "Elixir": "#6a4a8a",
    "Vue": "#3fa477",
    "Svelte": "#d9532c",
}

IGNORED_DIRS = {
    ".git", "node_modules", "vendor", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "out", "target", ".next", ".nuxt", "coverage", ".tox",
    ".mypy_cache", ".pytest_cache", "site-packages", "bower_components",
    ".idea", ".vscode", "third_party", "migrations", ".dna-workdir", "staticfiles",
}

MINIFIED = re.compile(r"\.min\.(js|css)$|\.bundle\.js$|-lock\.|\.lock$")

TEST_PATTERNS = [
    re.compile(r"(^|/)(tests?|__tests__|spec|specs|testing)/"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"_test\.(py|go|rb|exs?)$"),
    re.compile(r"\.(test|spec)\.[jt]sx?$"),
    re.compile(r"(^|/)[^/]*Tests?\.(java|kt|cs|swift|scala)$"),
    re.compile(r"(^|/)conftest\.py$"),
]

CI_MARKERS = [
    ".github/workflows", ".gitlab-ci.yml", ".circleci", "Jenkinsfile",
    ".travis.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".drone.yml",
]


def language_for(path):
    suffix = PurePosixPath(path).suffix.lower()
    entry = LANGUAGES.get(suffix)
    return entry


def is_ignored(path):
    parts = PurePosixPath(path).parts
    if any(part in IGNORED_DIRS for part in parts[:-1]):
        return True
    return bool(MINIFIED.search(path))


def is_test_path(path):
    path = path.replace("\\", "/")
    return any(p.search(path) for p in TEST_PATTERNS)


def top_level_module(path):
    """The folder a file belongs to, used for the architecture breakdown."""
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        return "(root)"
    # Skip a conventional "src" wrapper so the breakdown is more meaningful.
    if parts[0] in {"src", "lib", "app", "pkg"} and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]
