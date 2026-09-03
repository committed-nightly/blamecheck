"""Thin wrappers over the ``git`` binary.

Everything here shells out. The point of the tool is to agree with what git
will actually do with your ignore file, so it asks git rather than reading
object files itself.

Three of these are batched deliberately. An ignore file with two hundred
entries is normal in a repo that has been formatted a few times, and doing
this one fork per revision turns a check that should be instant into something
people take out of CI.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(RuntimeError):
    """git was missing, unhappy, or pointed at something that isn't a repo."""


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    date: str  # ISO 8601, as git formatted it
    subject: str

    @property
    def short(self) -> str:
        return self.sha[:9]


def run_git(args: list[str], cwd: str, *, stdin: str | None = None) -> str:
    """Run git and return stdout, or raise GitError."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=stdin.encode("utf-8") if stdin is not None else None,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the box
        raise GitError("git is not on PATH") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(stderr or f"git {' '.join(args)} failed")
    return proc.stdout.decode("utf-8", "replace")


def repo_root(cwd: str) -> str:
    """Absolute path of the working tree containing ``cwd``."""
    return run_git(["rev-parse", "--show-toplevel"], cwd).strip()


def object_format(cwd: str) -> str:
    """``sha1`` or ``sha256``."""
    try:
        return run_git(["rev-parse", "--show-object-format"], cwd).strip() or "sha1"
    except GitError:  # pragma: no cover - only on git older than 2.29
        return "sha1"


def is_shallow(cwd: str) -> bool:
    return run_git(["rev-parse", "--is-shallow-repository"], cwd).strip() == "true"


def resolve(rev: str, cwd: str) -> str:
    """Resolve a revision to a full commit sha, raising GitError if unknown."""
    out = run_git(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd).strip()
    if not out:
        raise GitError(f"no such revision: {rev}")
    return out


def object_types(names: list[str], cwd: str) -> dict[str, tuple[str, str] | None]:
    """Look up many object names at once.

    Maps each name to ``(sha, type)``, or to None if the repository has no
    object with that name. One ``cat-file --batch-check`` for the whole file.
    """
    if not names:
        return {}
    out = run_git(["cat-file", "--batch-check"], cwd, stdin="\n".join(names) + "\n")

    found: dict[str, tuple[str, str] | None] = {}
    for name, line in zip(names, out.splitlines()):
        fields = line.split()
        # A missing object echoes the name back: "<name> missing". Anything
        # else is "<sha> <type> <size>".
        if len(fields) >= 2 and fields[1] != "missing":
            found[name] = (fields[0], fields[1])
        else:
            found[name] = None
    return found


def peel_to_commit(name: str, cwd: str) -> str | None:
    """Follow a tag object down to the commit it points at, if it points at one."""
    try:
        return resolve(name, cwd)
    except GitError:
        return None


def unreachable_from(shas: list[str], ref: str, cwd: str) -> set[str]:
    """Which of ``shas`` are not ancestors of ``ref``.

    ``rev-list --no-walk A B --not <ref>`` prints exactly the given commits
    that ``ref`` cannot reach, in one call, without walking history for each
    one separately.
    """
    if not shas:
        return set()
    out = run_git(
        ["rev-list", "--no-walk", *dict.fromkeys(shas), "--not", ref], cwd
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def describe(shas: list[str], cwd: str) -> dict[str, Commit]:
    """Subject, author and date for many commits at once."""
    unique = list(dict.fromkeys(shas))
    if not unique:
        return {}
    out = run_git(
        [
            "log",
            "--no-walk=unsorted",
            "--format=%H%x00%an%x00%aI%x00%s",
            *unique,
        ],
        cwd,
    )
    commits: dict[str, Commit] = {}
    for line in out.splitlines():
        fields = line.split("\x00")
        if len(fields) < 4:
            continue
        sha, author, date, subject = fields[:4]
        commits[sha] = Commit(sha=sha, author=author, date=date, subject=subject)
    return commits


def ignore_revs_config(cwd: str) -> str | None:
    """The repo's ``blame.ignoreRevsFile`` setting, if it has one."""
    try:
        return run_git(["config", "--get", "blame.ignoreRevsFile"], cwd).strip() or None
    except GitError:
        # `git config --get` exits 1 when the key is unset, which run_git
        # raises on. Unset is the answer, not a failure.
        return None


def set_ignore_revs_config(path: str, cwd: str) -> None:
    run_git(["config", "blame.ignoreRevsFile", path], cwd)
