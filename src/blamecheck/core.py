"""Classify every revision in the ignore file against the repository.

The four failures worth having a tool for, in the order they hurt:

``malformed``
    git cannot parse the line, so ``git blame`` exits 128 with "invalid object
    name" for every file in the repository, for everyone, including whatever
    your editor runs to draw the gutter. Loud, total, and at least you find out.

``missing``
    A well-formed name for an object that isn't here. git parses hex and never
    looks the object up, so the line is carried into the ignore set and does
    nothing. No error, no warning, no output difference you would notice. This
    is what a squash merge does to you: the sha you copied lived on the branch,
    and the commit that landed is a different one.

``not-a-commit``
    The name resolves, but to a tree or a blob. Same silence as ``missing``.
    Annotated tags are fine -- git peels them -- so this is nearly always a
    ``git rev-parse HEAD^{tree}`` that went in by accident.

``unreachable``
    A real commit that ``ref`` cannot reach. It is in your clone because you
    still have the branch, and it is not in a fresh clone or on CI, so the file
    works for you and quietly stops working for everybody else. The other half
    of the squash-merge story.

And one thing that is only untidy:

``duplicate``
    The same commit named twice, or named once by its sha and once by a tag
    that points at it. Harmless to git. Reported because it is usually the
    residue of two people fixing the same rot independently, which is worth
    knowing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import gitcmd, revsfile
from .gitcmd import Commit, GitError

OK = "ok"
MALFORMED = "malformed"
MISSING = "missing"
NOT_A_COMMIT = "not-a-commit"
UNREACHABLE = "unreachable"
DUPLICATE = "duplicate"

# Anything here means the file is not doing what it says it does.
ERRORS = (MALFORMED, MISSING, NOT_A_COMMIT, UNREACHABLE)
WARNINGS = (DUPLICATE,)

DEFAULT_FILE = ".git-blame-ignore-revs"


@dataclass(frozen=True)
class Finding:
    lineno: int
    #: The object name as written, or the whole line if it did not parse.
    text: str
    status: str
    #: The commit the line resolves to, when it resolves to one at all.
    commit: Commit | None = None
    #: For a duplicate, the line that already named this commit.
    first_seen: int | None = None

    @property
    def is_error(self) -> bool:
        return self.status in ERRORS

    @property
    def is_warning(self) -> bool:
        return self.status in WARNINGS


@dataclass(frozen=True)
class Report:
    path: str
    ref: str
    findings: list[Finding]

    @property
    def revs(self) -> int:
        return len(self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.is_error]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.is_warning]

    @property
    def ok(self) -> bool:
        return not self.errors


def read_file(root: str, path: str) -> str:
    """Read the ignore file, or raise GitError with something actionable."""
    full = path if os.path.isabs(path) else os.path.join(root, path)
    if os.path.isdir(full):
        raise GitError(f"{path} is a directory")
    try:
        with open(full, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        raise GitError(f"no such file: {path}") from exc
    except OSError as exc:  # pragma: no cover - permissions, mostly
        raise GitError(f"could not read {path}: {exc}") from exc


def check(cwd: str, *, path: str = DEFAULT_FILE, ref: str = "HEAD") -> Report:
    """Classify every revision in ``path`` against ``ref``."""
    root = gitcmd.repo_root(cwd)
    text = read_file(root, path)
    hex_len = revsfile.HEX_LEN.get(
        gitcmd.object_format(cwd), revsfile.DEFAULT_HEX_LEN
    )
    lines = revsfile.parse(text, hex_len=hex_len)

    names = [line.name for line in lines if line.name is not None]
    types = gitcmd.object_types(names, cwd)

    # First pass: everything decidable from the object store alone.
    staged: list[tuple[revsfile.Line, str, str | None]] = []
    for line in lines:
        if line.name is None:
            staged.append((line, MALFORMED, None))
            continue
        entry = types.get(line.name)
        if entry is None:
            staged.append((line, MISSING, None))
            continue
        sha, kind = entry
        if kind == "commit":
            staged.append((line, OK, sha))
        elif kind == "tag":
            peeled = gitcmd.peel_to_commit(line.name, cwd)
            staged.append(
                (line, OK, peeled) if peeled else (line, NOT_A_COMMIT, None)
            )
        else:
            staged.append((line, NOT_A_COMMIT, None))

    # Second pass: reachability, in one call for the whole file.
    resolved = [sha for _, status, sha in staged if status == OK and sha]
    unreachable = gitcmd.unreachable_from(resolved, ref, cwd)

    # Details only for commits we resolved. There is nothing to describe about
    # a sha that does not exist, which is exactly what makes ``missing`` the
    # hardest of these to chase down by hand.
    details = gitcmd.describe(resolved, cwd)

    findings: list[Finding] = []
    seen: dict[str, int] = {}
    for line, status, sha in staged:
        text_shown = line.name if line.name is not None else line.raw.strip()
        commit = details.get(sha) if sha else None
        first_seen: int | None = None

        if status == OK and sha in unreachable:
            status = UNREACHABLE
        elif status == OK and sha is not None:
            if sha in seen:
                status = DUPLICATE
                first_seen = seen[sha]
            else:
                seen[sha] = line.lineno

        findings.append(
            Finding(
                lineno=line.lineno,
                text=text_shown,
                status=status,
                commit=commit,
                first_seen=first_seen,
            )
        )

    return Report(path=path, ref=ref, findings=findings)
