"""
Per-file source analysis.

Python files are parsed with the standard library ``ast`` module, which
gives exact function boundaries and branch counts. For brace-based languages
we strip strings and comments, then match braces to find each function body
and count decision points inside it. It is not a compiler, but it is
consistent across snapshots, and consistency is what matters for trends.
"""

import ast
import math
import re
import warnings
from dataclasses import dataclass, field

LONG_FUNCTION_LINES = 60
COMPLEX_FUNCTION_CC = 10
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
TOKEN_RE = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|&&|\|\||->|=>|[-+*/%=<>!&|^~?:]")


@dataclass
class FunctionInfo:
    name: str
    line: int
    length: int
    complexity: int


@dataclass
class FileReport:
    path: str
    language: str
    code_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    functions: list = field(default_factory=list)
    file_complexity: int = 1
    max_nesting: int = 0
    maintainability: float = 100.0
    todo_count: int = 0
    normalized_lines: list = field(default_factory=list)  # for duplication

    @property
    def function_count(self):
        return len(self.functions)

    @property
    def complexity(self):
        if self.functions:
            return sum(f.complexity for f in self.functions)
        return self.file_complexity

    @property
    def max_function(self):
        if not self.functions:
            return None
        return max(self.functions, key=lambda f: f.complexity)


# ---------------------------------------------------------------- helpers

def _strip_code(text, line_comments, block):
    """Blank out comments and string literals but keep line numbers intact."""
    out = []
    i, n = 0, len(text)
    block_open, block_close = block if block else (None, None)
    while i < n:
        ch = text[i]
        if block_open and text.startswith(block_open, i):
            end = text.find(block_close, i + len(block_open))
            end = n if end == -1 else end + len(block_close)
            out.append(re.sub(r"[^\n]", " ", text[i:end]))
            i = end
            continue
        matched_line_comment = next((tok for tok in line_comments if text.startswith(tok, i)), None)
        if matched_line_comment:
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
            continue
        if ch in "\"'`":
            quote = ch
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and quote != "`":
                    break
                j += 1
            j = min(j + 1, n)
            segment = text[i:j]
            out.append(quote + re.sub(r"[^\n]", " ", segment[1:-1]) + (quote if len(segment) > 1 else ""))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _count_lines(original_lines, clean_lines, extra_comment_lines=()):
    code = comment = blank = 0
    extra = set(extra_comment_lines)
    for idx, (orig, clean) in enumerate(zip(original_lines, clean_lines), start=1):
        if not orig.strip():
            blank += 1
        elif idx in extra or not clean.strip():
            comment += 1
        else:
            code += 1
    return code, comment, blank


def _normalize_for_duplication(lines):
    result = []
    for number, line in enumerate(lines, start=1):
        norm = re.sub(r"\s+", " ", line.strip())
        if len(norm) >= 8 and norm not in {"else {", "} else {", "return;", "pass", "break;"}:
            result.append((number, norm))
    return result


def _halstead_volume(clean_text):
    tokens = TOKEN_RE.findall(clean_text)
    if not tokens:
        return 0.0
    vocabulary = len(set(tokens))
    return len(tokens) * math.log2(max(vocabulary, 2))


def maintainability_index(volume, complexity, loc, comment_share=0.0):
    """SEI maintainability index (with the comment term), normalised to 0-100."""
    if loc <= 0:
        return 100.0
    raw = (171 - 5.2 * math.log(max(volume, 1)) - 0.23 * complexity - 16.2 * math.log(loc)
           + 50 * math.sin(math.sqrt(2.4 * comment_share)))
    return round(max(0.0, min(100.0, raw * 100 / 171)), 1)


# ---------------------------------------------------------------- Python

class _PyComplexity(ast.NodeVisitor):
    BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)
    NESTERS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)

    def __init__(self):
        self.complexity = 1
        self.depth = 0
        self.max_depth = 0

    def generic_visit(self, node):
        if isinstance(node, self.BRANCHES):
            self.complexity += 1
        elif isinstance(node, ast.BoolOp):
            self.complexity += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            self.complexity += 1 + len(node.ifs)
        elif hasattr(ast, "match_case") and isinstance(node, ast.match_case):
            self.complexity += 1
        nests = isinstance(node, self.NESTERS)
        if nests:
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)
        super().generic_visit(node)
        if nests:
            self.depth -= 1

    def visit_FunctionDef(self, node):  # nested functions are measured separately
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_Lambda = generic_visit


def _python_functions(tree):
    functions, max_nesting = [], 0

    def walk(node, prefix=""):
        nonlocal max_nesting
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visitor = _PyComplexity()
                for stmt in child.body:
                    visitor.visit(stmt)
                end = getattr(child, "end_lineno", child.lineno)
                functions.append(FunctionInfo(
                    name=f"{prefix}{child.name}",
                    line=child.lineno,
                    length=end - child.lineno + 1,
                    complexity=visitor.complexity,
                ))
                max_nesting = max(max_nesting, visitor.max_depth)
                walk(child, prefix=f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree)
    return functions, max_nesting


def _docstring_lines(tree):
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                start, end = body[0].lineno, getattr(body[0], "end_lineno", body[0].lineno)
                lines.update(range(start, end + 1))
    return lines


def analyze_python(path, text):
    report = FileReport(path=path, language="Python")
    lines = text.splitlines()
    clean = _strip_code(text, ("#",), None)
    clean_lines = clean.splitlines()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        tree = None

    if tree is not None:
        report.functions, report.max_nesting = _python_functions(tree)
        doc_lines = _docstring_lines(tree)
        module_visitor = _PyComplexity()
        module_visitor.visit(tree)
        report.file_complexity = module_visitor.complexity
    else:
        doc_lines = set()
        report.file_complexity = 1 + len(re.findall(r"\b(if|elif|for|while|except|and|or)\b", clean))

    report.code_lines, report.comment_lines, report.blank_lines = _count_lines(lines, clean_lines, doc_lines)
    _finish(report, text, clean, lines)
    return report


# ---------------------------------------------------------------- brace languages

KEYWORDS_NOT_FUNCTIONS = {
    "if", "for", "while", "switch", "catch", "return", "function", "else", "do",
    "try", "with", "new", "typeof", "sizeof", "await", "elif", "foreach", "using", "lock",
}
DECISION_RE = re.compile(r"\b(if|for|while|case|catch|foreach|elif|elsif|unless|until|when|except|guard)\b|&&|\|\||\?\?|\?(?![.:\]])")

FUNCTION_PATTERNS = {
    "JavaScript": [
        re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"\b([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s+)?(?:function\b[^(]*\(|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
        re.compile(r"^\s*(?:static\s+|async\s+|get\s+|set\s+|public\s+|private\s+|protected\s+)*([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*(?::\s*[^={]+)?\{"),
    ],
    "Go": [re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]")],
    "Rust": [re.compile(r"\bfn\s+([A-Za-z_]\w*)")],
    "PHP": [re.compile(r"\bfunction\s+&?\s*([A-Za-z_]\w*)\s*\(")],
    "Swift": [re.compile(r"\bfunc\s+([A-Za-z_]\w*)")],
    "Kotlin": [re.compile(r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(")],
    "Scala": [re.compile(r"\bdef\s+([A-Za-z_]\w*)")],
    "C-like": [re.compile(
        r"^\s*(?:[\w<>\[\],.*&:?]+\s+)+\**([A-Za-z_~]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:throws\s+[\w.,\s]+)?(?:noexcept\s*)?\{"
    )],
}
FUNCTION_PATTERNS["TypeScript"] = FUNCTION_PATTERNS["JavaScript"]
FUNCTION_PATTERNS["Vue"] = FUNCTION_PATTERNS["JavaScript"]
FUNCTION_PATTERNS["Svelte"] = FUNCTION_PATTERNS["JavaScript"]
FUNCTION_PATTERNS["Dart"] = FUNCTION_PATTERNS["C-like"]
for _lang in ("Java", "C", "C++", "C#"):
    FUNCTION_PATTERNS[_lang] = FUNCTION_PATTERNS["C-like"]


def _line_offsets(text):
    offsets, pos = [0], 0
    for line in text.split("\n"):
        pos += len(line) + 1
        offsets.append(pos)
    return offsets


def _brace_functions(language, clean):
    patterns = FUNCTION_PATTERNS.get(language, [])
    if not patterns:
        return [], 0
    offsets = _line_offsets(clean)
    clean_lines = clean.split("\n")
    seen_starts = set()
    functions = []
    max_nesting = 0
    covered_until = -1  # skip nested functions: they are part of the parent body

    for line_no, line in enumerate(clean_lines, start=1):
        start_offset = offsets[line_no - 1]
        if start_offset < covered_until:
            continue
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            name = match.group(1)
            if name in KEYWORDS_NOT_FUNCTIONS:
                continue
            brace = clean.find("{", start_offset + match.start())
            # The opening brace must be close to the signature, otherwise this
            # was a call or a declaration without a body.
            if brace == -1 or clean.count("\n", start_offset, brace) > 3 or ";" in clean[start_offset + match.end():brace]:
                continue
            depth, i, deepest = 0, brace, 0
            while i < len(clean):
                c = clean[i]
                if c == "{":
                    depth += 1
                    deepest = max(deepest, depth)
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            if (line_no, name) in seen_starts:
                break
            seen_starts.add((line_no, name))
            body = clean[brace:i + 1]
            end_line = clean.count("\n", 0, i) + 1
            functions.append(FunctionInfo(
                name=name,
                line=line_no,
                length=end_line - line_no + 1,
                complexity=1 + len(DECISION_RE.findall(body)),
            ))
            max_nesting = max(max_nesting, max(deepest - 1, 0))
            covered_until = i
            break
    return functions, max_nesting


def analyze_generic(path, text, language, line_comments, block, style):
    report = FileReport(path=path, language=language)
    lines = text.splitlines()
    clean = _strip_code(text, line_comments, block)
    clean_lines = clean.splitlines()
    report.code_lines, report.comment_lines, report.blank_lines = _count_lines(lines, clean_lines)
    report.file_complexity = 1 + len(DECISION_RE.findall(clean))

    if style == "brace":
        report.functions, report.max_nesting = _brace_functions(language, clean)
    else:
        # Indentation languages: count definitions and spread complexity.
        defs = [(m.group(1), clean.count("\n", 0, m.start()) + 1)
                for m in re.finditer(r"^\s*(?:def|function|defp)\s+([\w?!.]+)", clean, re.M)]
        if defs:
            share = max(1, round(report.file_complexity / len(defs)))
            for idx, (name, line) in enumerate(defs):
                end = defs[idx + 1][1] - 1 if idx + 1 < len(defs) else len(lines)
                report.functions.append(FunctionInfo(name, line, end - line + 1, share))
        indents = [len(l) - len(l.lstrip()) for l in clean_lines if l.strip()]
        report.max_nesting = (max(indents) // 2) if indents else 0

    _finish(report, text, clean, lines)
    return report


def _finish(report, text, clean, lines):
    report.todo_count = len(TODO_RE.findall(text))
    report.maintainability = maintainability_index(
        _halstead_volume(clean), report.complexity, max(report.code_lines, 1),
        report.comment_lines / max(report.code_lines + report.comment_lines, 1),
    )
    report.normalized_lines = _normalize_for_duplication(lines)


def analyze_file(path, text, language_entry):
    language, line_comments, block, style = language_entry
    if style == "python":
        return analyze_python(path, text)
    return analyze_generic(path, text, language, line_comments, block, style)
