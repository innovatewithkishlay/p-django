"""
Lightweight static checks for risky patterns.

This is a linter, not a vulnerability scanner: each rule looks for a
pattern that is very often a problem and explains why. Findings carry the
line number so they can be checked by a human quickly.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

PLACEHOLDER = re.compile(
    r"(?i)(change.?me|example|dummy|placeholder|your[_-]|xxx+|\*\*\*|<[^>]+>|\$\{|\{\{|os\.environ|getenv|process\.env|test|fake|sample|redacted|insecure|dev-only)"
)


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str  # critical | high | medium | low
    pattern: re.Pattern
    languages: tuple  # empty = every language
    explanation: str
    recommendation: str
    skip_tests: bool = True
    check_placeholder: bool = False


RULES = [
    Rule(
        "hardcoded-secret", "Credential written directly in code", "critical",
        re.compile(r"""(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|auth[_-]?token|access[_-]?token|private[_-]?key)\b["']?\s*[:=]\s*["']([^"'\s]{8,})["']"""),
        (), "A value that looks like a real credential is committed to the repository. Anyone with read access "
            "(and every fork, clone and CI log) now has it, and git history keeps it even after you delete the line.",
        "Rotate the credential first, then load it from an environment variable or a secrets manager.",
        check_placeholder=True,
    ),
    Rule(
        "aws-key", "AWS access key in source", "critical",
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), (),
        "This matches the format of an AWS access key id. Leaked AWS keys are scraped from public repositories within minutes.",
        "Deactivate the key in IAM, then move it to the environment or an AWS profile.", skip_tests=False,
    ),
    Rule(
        "private-key", "Private key committed", "critical",
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), (),
        "A private key is stored in the repository, so whoever can read the code can impersonate that key's owner.",
        "Remove the file, rewrite it out of history, and issue a new key pair.", skip_tests=False,
    ),
    Rule(
        "github-token", "GitHub token in source", "critical",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), (),
        "This looks like a GitHub personal access or app token.",
        "Revoke it in GitHub settings and read it from the environment instead.", skip_tests=False,
    ),
    Rule(
        "py-eval", "eval()/exec() on dynamic input", "high",
        re.compile(r"(?<![\w.])(eval|exec)\s*\((?!\s*['\"])"), ("Python",),
        "eval and exec run arbitrary code. If any part of the argument can be influenced by a user, it becomes remote code execution.",
        "Use ast.literal_eval for data, a dictionary of allowed functions for dispatch, or a proper parser.",
    ),
    Rule(
        "py-shell", "Shell command built with shell=True", "high",
        re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True|\bos\.system\s*\(|\bos\.popen\s*\("), ("Python",),
        "Passing a string to the shell lets special characters in the input run extra commands (command injection).",
        "Pass the command as a list to subprocess.run without shell=True, and validate any user-supplied part.",
    ),
    Rule(
        "py-sql", "SQL built with string formatting", "high",
        re.compile(r"""\.(execute|raw|executemany)\s*\(\s*(f["']|["'][^"']*["']\s*(%|\.format\b|\+))"""), ("Python",),
        "Building SQL by formatting strings is the classic way SQL injection happens.",
        "Use query parameters (cursor.execute(sql, params)) or the ORM.",
    ),
    Rule(
        "py-pickle", "Unpickling data", "medium",
        re.compile(r"\b(pickle|cPickle|dill)\.loads?\s*\("), ("Python",),
        "pickle can execute code while loading. Loading a pickle from an untrusted place is equivalent to running their code.",
        "Use JSON or another data-only format for anything that crosses a trust boundary.",
    ),
    Rule(
        "py-yaml", "yaml.load without a safe loader", "medium",
        re.compile(r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*(yaml\.)?(Safe|CSafe)Loader)"), ("Python",),
        "yaml.load with the default loader can construct arbitrary Python objects.",
        "Use yaml.safe_load.",
    ),
    Rule(
        "weak-hash", "Weak hash algorithm", "medium",
        re.compile(r"\bhashlib\.(md5|sha1)\s*\(|\bcreateHash\(\s*['\"](md5|sha1)['\"]|MessageDigest\.getInstance\(\s*\"(MD5|SHA-?1)\""), (),
        "MD5 and SHA-1 are broken for security purposes (collisions are practical).",
        "Use SHA-256 or better; for passwords use a dedicated KDF such as bcrypt, scrypt or Argon2.",
    ),
    Rule(
        "tls-off", "TLS certificate checks disabled", "high",
        re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED"), (),
        "Turning off certificate verification makes HTTPS traffic open to man-in-the-middle attacks.",
        "Keep verification on. If you need a private CA, point the client at its bundle instead.",
    ),
    Rule(
        "django-debug", "DEBUG switched on in settings", "medium",
        re.compile(r"^\s*DEBUG\s*=\s*True\b", re.M), ("Python",),
        "With DEBUG on, Django shows stack traces, settings and SQL to anyone who triggers an error.",
        "Read DEBUG from the environment and default it to False in production settings.",
    ),
    Rule(
        "django-csrf-exempt", "CSRF protection switched off for a view", "low",
        re.compile(r"@csrf_exempt\b"), ("Python",),
        "csrf_exempt removes Django's protection against cross-site request forgery for that view.",
        "Keep it only for endpoints authenticated by tokens (not cookies), and document why.",
    ),
    Rule(
        "js-eval", "eval() / new Function()", "high",
        re.compile(r"(?<![\w.])eval\s*\(|\bnew\s+Function\s*\("), ("JavaScript", "TypeScript", "Vue", "Svelte"),
        "Evaluating strings as code turns any injected text into executable script.",
        "Parse data with JSON.parse and avoid generating code at runtime.",
    ),
    Rule(
        "js-html", "Raw HTML injected into the page", "medium",
        re.compile(r"\.innerHTML\s*=|\bdangerouslySetInnerHTML\b|document\.write\s*\(|\bv-html\s*="),
        ("JavaScript", "TypeScript", "Vue", "Svelte"),
        "Writing raw HTML from variables is the most common source of cross-site scripting (XSS).",
        "Use textContent, framework bindings, or sanitise with a library such as DOMPurify.",
    ),
    Rule(
        "js-exec", "Shell command from Node", "medium",
        re.compile(r"child_process['\"]?\)?\.exec(Sync)?\s*\(|\bexecSync\s*\("), ("JavaScript", "TypeScript"),
        "exec runs the command through a shell, so unescaped input can run extra commands.",
        "Prefer execFile/spawn with an argument array.",
    ),
]


def looks_random(value):
    """Real secrets are high-entropy and mix character classes; 'secret-key' is not."""
    classes = sum(bool(re.search(p, value)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    counts = Counter(value)
    entropy = -sum(c / len(value) * math.log2(c / len(value)) for c in counts.values())
    return classes >= 2 and entropy >= 3.0 and not re.fullmatch(r"[a-z]+([-_][a-z]+)*", value)


@dataclass
class SecurityHit:
    rule: Rule
    path: str
    line: int
    snippet: str


def scan(path, text, language, is_test):
    hits = []
    applicable = [r for r in RULES if (not r.languages or language in r.languages) and not (is_test and r.skip_tests)]
    if not applicable:
        return hits
    lines = text.splitlines()
    for rule in applicable:
        if rule.pattern.flags & re.M:
            for m in rule.pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                hits.append(SecurityHit(rule, path, line_no, lines[line_no - 1].strip()[:160] if lines else ""))
            continue
        for number, line in enumerate(lines, start=1):
            if len(line) > 600:
                continue
            match = rule.pattern.search(line)
            if not match:
                continue
            if rule.check_placeholder and (PLACEHOLDER.search(line) or not looks_random(match.groups()[-1])):
                continue
            hits.append(SecurityHit(rule, path, number, line.strip()[:160]))
    # Settings DEBUG flag only matters in settings modules.
    return [h for h in hits if h.rule.id != "django-debug" or "settings" in path]
