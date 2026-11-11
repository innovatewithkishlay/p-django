"""
Read dependency manifests and judge how risky they are.

We do not call any package registry (analysis has to work offline), so the
judgement is structural: is the version pinned, is it a package that is
known to be deprecated or abandoned, and how many dependencies are there.
"""

import json
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

# Packages with a well known reason to move away from them.
KNOWN_RISKY = {
    "python": {
        "pycrypto": "Unmaintained since 2013 with known vulnerabilities; use pycryptodome or cryptography.",
        "nose": "Unmaintained and broken on modern Python; use pytest.",
        "django-rest-swagger": "Archived; use drf-spectacular.",
        "simplejson": "The standard json module covers this today.",
        "mock": "Part of the standard library as unittest.mock since Python 3.3.",
        "six": "Only needed for Python 2 compatibility.",
        "pyyaml<5.4": "Old PyYAML releases allow code execution through yaml.load.",
    },
    "npm": {
        "request": "Deprecated since 2020; use fetch, undici or axios.",
        "node-sass": "Deprecated; use the sass package (Dart Sass).",
        "moment": "In maintenance mode and large; consider date-fns, dayjs or Temporal.",
        "left-pad": "Use String.prototype.padStart.",
        "tslint": "Deprecated in favour of ESLint with typescript-eslint.",
        "babel-eslint": "Renamed to @babel/eslint-parser.",
        "core-js@2": "Old major with known performance issues.",
        "uuid@3": "Very old major; upgrade.",
        "querystring": "Deprecated Node API; use URLSearchParams.",
    },
}

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")


def _pin_level(spec, ecosystem):
    spec = (spec or "").strip().strip(",;").split(";")[0].strip()
    if not spec or spec in {"*", "latest", "x"}:
        return "unpinned"
    if ecosystem == "python":
        if spec.startswith("==") and "*" not in spec:
            return "pinned"
        if "<" in spec or spec.startswith("~="):
            return "ranged"
        if spec.startswith(">"):
            return "unpinned"
        return "ranged"
    if ecosystem == "npm":
        if re.match(r"^\d", spec):
            return "pinned"
        if spec[0] in "^~":
            return "ranged"
        if spec.startswith((">=", ">")) and "<" not in spec:
            return "unpinned"
        if spec.startswith(("git", "http", "file:", "link:", "workspace:")):
            return "ranged"
        return "ranged"
    if spec.startswith("="):
        return "pinned"
    return "ranged" if re.search(r"\d", spec) else "unpinned"


def _dep(name, spec, ecosystem, manifest, dev=False):
    name_l = name.lower()
    risk = None
    for key, reason in KNOWN_RISKY.get(ecosystem, {}).items():
        base = re.split(r"[<@]", key)[0]
        if base == name_l:
            if "@" in key:
                major = key.split("@")[1]
                if re.match(rf"^[\^~=]?{major}\.", spec or ""):
                    risk = reason
            elif "<" in key:
                if re.search(r"[<=]=?\s*[0-4]\.|==5\.[0-3]", spec or ""):
                    risk = reason
            else:
                risk = reason
    return {
        "name": name,
        "spec": (spec or "").strip() or "any",
        "ecosystem": ecosystem,
        "manifest": manifest,
        "dev": dev,
        "pin": _pin_level(spec, ecosystem),
        "risk": risk,
    }


def parse_requirements(text, manifest):
    deps = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http://", "https://")):
            continue
        m = _REQ_LINE.match(line)
        if m:
            deps.append(_dep(m.group(1), m.group(3), "python", manifest, dev="dev" in manifest or "test" in manifest))
    return deps


def parse_pyproject(text, manifest):
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    deps = []
    for line in data.get("project", {}).get("dependencies", []) or []:
        deps.extend(parse_requirements(line, manifest))
    for group, items in (data.get("project", {}).get("optional-dependencies", {}) or {}).items():
        for line in items:
            for d in parse_requirements(line, manifest):
                d["dev"] = True
                deps.append(d)
    poetry = data.get("tool", {}).get("poetry", {})
    for section, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (poetry.get(section, {}) or {}).items():
            if name.lower() == "python":
                continue
            if isinstance(spec, dict):
                spec = spec.get("version", "")
            spec = str(spec)
            spec = "==" + spec if re.match(r"^\d", spec) else spec.replace("^", "~=")
            deps.append(_dep(name, spec, "python", manifest, dev))
    return deps


def parse_pipfile(text, manifest):
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    deps = []
    for section, dev in (("packages", False), ("dev-packages", True)):
        for name, spec in (data.get(section, {}) or {}).items():
            if isinstance(spec, dict):
                spec = spec.get("version", "*")
            deps.append(_dep(name, str(spec), "python", manifest, dev))
    return deps


def parse_package_json(text, manifest):
    try:
        data = json.loads(text)
    except ValueError:
        return []
    deps = []
    for section, dev in (("dependencies", False), ("devDependencies", True), ("peerDependencies", False)):
        for name, spec in (data.get(section) or {}).items():
            deps.append(_dep(name, str(spec), "npm", manifest, dev))
    return deps


def parse_go_mod(text, manifest):
    deps = []
    in_block = False
    for line in text.splitlines():
        line = line.split("//")[0].strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):]
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            deps.append(_dep(parts[0], "=" + parts[1], "go", manifest))
    return deps


def parse_cargo(text, manifest):
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    deps = []
    for section, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, spec in (data.get(section, {}) or {}).items():
            if isinstance(spec, dict):
                spec = spec.get("version", "")
            spec = str(spec)
            deps.append(_dep(name, "=" + spec if spec.startswith("=") else spec, "cargo", manifest, dev))
    return deps


def parse_gemfile(text, manifest):
    deps = []
    for m in re.finditer(r"^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", text, re.M):
        spec = m.group(2) or ""
        deps.append(_dep(m.group(1), ("=" + spec.lstrip("= ")) if spec.startswith("=") or re.match(r"^\d", spec) else spec, "ruby", manifest))
    return deps


PARSERS = [
    (re.compile(r"(^|/)requirements[^/]*\.(txt|in)$"), parse_requirements),
    (re.compile(r"(^|/)pyproject\.toml$"), parse_pyproject),
    (re.compile(r"(^|/)Pipfile$"), parse_pipfile),
    (re.compile(r"(^|/)package\.json$"), parse_package_json),
    (re.compile(r"(^|/)go\.mod$"), parse_go_mod),
    (re.compile(r"(^|/)Cargo\.toml$"), parse_cargo),
    (re.compile(r"(^|/)Gemfile$"), parse_gemfile),
]


def parser_for(path):
    for pattern, parser in PARSERS:
        if pattern.search(path):
            return parser
    return None


def summarize(deps):
    unique = {}
    for d in deps:
        key = (d["ecosystem"], d["name"].lower())
        if key not in unique or unique[key]["pin"] == "unpinned":
            unique[key] = d
    items = sorted(unique.values(), key=lambda d: (d["risk"] is None, d["pin"] != "unpinned", d["name"].lower()))
    return items
