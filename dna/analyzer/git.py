"""Thin wrapper around the git command line."""

import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[#?].*)?$"
)
SHORT_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


class GitError(RuntimeError):
    pass


@dataclass
class Commit:
    sha: str
    author: str
    date: datetime
    message: str
    tag: str = ""


def parse_repo_input(value, allow_local=False):
    """Turn whatever the user typed into (source, owner, name, clone_url)."""
    value = (value or "").strip()
    m = GITHUB_RE.match(value)
    if m:
        owner, name = m.group(1), m.group(2)
        return "github", owner, name, f"https://github.com/{owner}/{name}.git"
    m = SHORT_RE.match(value)
    if m and not value.startswith((".", "/")):
        owner, name = m.group(1), m.group(2)
        return "github", owner, name, f"https://github.com/{owner}/{name}.git"
    if value.startswith(("https://", "http://", "git@", "ssh://")) and value.rstrip("/").endswith(".git") or \
            value.startswith(("https://gitlab.com/", "https://bitbucket.org/", "https://codeberg.org/")):
        name = value.rstrip("/").split("/")[-1].removesuffix(".git")
        owner = value.rstrip("/").split("/")[-2].split(":")[-1]
        return "git", owner, name, value
    if allow_local:
        path = Path(os.path.expanduser(value))
        if path.is_dir() and (path / ".git").exists():
            return "local", "", path.resolve().name, str(path.resolve())
    raise ValueError(
        "That doesn't look like a GitHub repository. Try something like "
        "https://github.com/pallets/flask or just pallets/flask."
    )


def run(args, cwd=None, timeout=600):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo", "LC_ALL": "C"}
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env, errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} took too long and was stopped.") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        hint = message[-1] if message else "unknown error"
        if "Repository not found" in result.stderr or "could not read Username" in result.stderr:
            hint = "Repository not found, or it is private."
        raise GitError(f"git {args[0]} failed: {hint}")
    return result.stdout


def clone_or_update(clone_url, dest):
    dest = Path(dest)
    if (dest / ".git").exists():
        try:
            run(["fetch", "--quiet", "--tags", "--force", "origin"], cwd=dest)
            head = default_branch(dest)
            run(["checkout", "-q", "-f", head], cwd=dest)
            run(["reset", "-q", "--hard", f"origin/{head}"], cwd=dest)
            return dest
        except GitError:
            shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["clone", "--quiet", "--filter=blob:none", "--no-tags", clone_url, str(dest)], timeout=900)
    except GitError:
        shutil.rmtree(dest, ignore_errors=True)
        run(["clone", "--quiet", clone_url, str(dest)], timeout=900)
    try:
        run(["fetch", "--quiet", "--tags", "origin"], cwd=dest)
    except GitError:
        pass
    return dest


def default_branch(repo):
    try:
        ref = run(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd=repo).strip()
        return ref.rsplit("/", 1)[-1]
    except GitError:
        return run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()


def history(repo, ref="HEAD", limit=5000):
    """First-parent history of ``ref``, oldest first."""
    out = run(["log", "--first-parent", f"-n{limit}", "--format=%H%x1f%an%x1f%cI%x1f%s%x1e", ref], cwd=repo)
    tags = tag_map(repo)
    commits = []
    for record in out.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        sha, author, date, message = (record.split("\x1f") + ["", "", "", ""])[:4]
        commits.append(Commit(sha, author, datetime.fromisoformat(date), message[:500], tags.get(sha, "")))
    commits.reverse()
    return commits


def tag_map(repo):
    try:
        out = run(["for-each-ref", "refs/tags", "--format=%(objectname) %(*objectname) %(refname:short)"], cwd=repo)
    except GitError:
        return {}
    result = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            sha, peeled, name = parts
        elif len(parts) == 2:
            sha, name = parts
            peeled = ""
        else:
            continue
        result.setdefault(peeled or sha, name)
    return result


def sample_commits(commits, count):
    """Pick ``count`` commits spread across history, always keeping the newest.

    Tagged releases are preferred when they are close to an evenly spaced
    point, because "v2.1 vs v2.0" means more to people than two hashes.
    """
    if len(commits) <= count:
        return list(commits)
    if count <= 1:
        return [commits[-1]]
    picked = []
    last_index = len(commits) - 1
    tagged = [i for i, c in enumerate(commits) if c.tag]
    window = max(1, len(commits) // (count * 3))
    for k in range(count):
        target = round(k * last_index / (count - 1))
        near = [i for i in tagged if abs(i - target) <= window]
        index = min(near, key=lambda i: abs(i - target)) if near and k != count - 1 else target
        if index not in picked:
            picked.append(index)
    return [commits[i] for i in sorted(picked)]


def checkout(repo, sha):
    run(["-c", "advice.detachedHead=false", "checkout", "-q", "-f", sha], cwd=repo, timeout=900)
    run(["clean", "-q", "-f", "-d"], cwd=repo)


def tracked_files(repo):
    out = run(["ls-files", "-z"], cwd=repo)
    return [p for p in out.split("\x00") if p]


def churn(repo, sha, max_commits=300):
    """How often each file changed, and by how many people, in recent history."""
    out = run(["log", f"-n{max_commits}", "--no-merges", "--format=@%an", "--name-only", sha], cwd=repo, timeout=300)
    changes = Counter()
    authors = defaultdict(set)
    author = ""
    for line in out.splitlines():
        if line.startswith("@"):
            author = line[1:]
        elif line.strip():
            changes[line.strip()] += 1
            authors[line.strip()].add(author)
    return changes, {path: len(names) for path, names in authors.items()}
