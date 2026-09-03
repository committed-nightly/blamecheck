"""Command line entry point.

Exit codes matter here, because the main use for this tool is as a CI gate:

    0  every revision in the file is real, reachable, and being ignored
    1  at least one is not
    2  the check could not run at all

2 is deliberately not 1, and very deliberately not 0. A shallow clone, a file
that isn't there, a --ref that doesn't resolve -- all of those mean nobody
looked, and "nobody looked" reported as a green tick is worse than no check at
all.

The one place that rule bends: an ignore file that exists and contains no
revisions is a **pass**, not an error. An empty ``.git-blame-ignore-revs`` is a
real and recommended thing to have -- it is what stops a global
``blame.ignoreRevsFile`` setting from killing blame in every repo that hasn't
been formatted yet -- and a file with nothing in it cannot have rotted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import gitcmd, revsfile
from .core import (
    DEFAULT_FILE,
    DUPLICATE,
    MALFORMED,
    MISSING,
    NOT_A_COMMIT,
    UNREACHABLE,
    Finding,
    Report,
    check,
    read_file,
)
from .gitcmd import GitError

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_ERROR = 2

# What git actually does about each one, which is the thing nobody knows and
# the reason this tool has a point of view at all.
HEADLINE = {
    MALFORMED: "git blame refuses to run at all",
    MISSING: "no such object here, so git ignores the line and blames as normal",
    NOT_A_COMMIT: "resolves to a tree or a blob, so the line does nothing",
    UNREACHABLE: "{ref} cannot reach it, so this works here and nowhere else",
}

BLAME_IS_DOWN = (
    "  git blame is refusing to run in this repository right now. Nothing else "
    "below\n  matters until the malformed line is fixed."
)

DETAIL = {
    MALFORMED: [
        "git parses this file strictly: one full-length object name per line,",
        "then nothing but whitespace or a # comment. Until this line is fixed",
        "every git blame in the repository exits 128 with 'invalid object",
        "name' -- including the one your editor runs to draw the gutter.",
        "A short sha is the usual cause. So is a note typed after the hash",
        "without a # in front of it.",
    ],
    MISSING: [
        "git parses hex and never looks the object up, so a name that resolves",
        "to nothing is carried into the ignore set and silently does nothing.",
        "The usual cause is a squash merge: the sha you copied was the one on",
        "the branch, and the commit that landed on the main line is a",
        "different object with a different name.",
    ],
    NOT_A_COMMIT: [
        "The name resolves, but to a tree or a blob. git ignores it as quietly",
        "as it ignores a name for nothing at all. Annotated tags are fine --",
        "git peels those -- so this is usually a tree sha pasted by mistake.",
    ],
    UNREACHABLE: [
        "The commit is in this clone because the branch it was made on is",
        "still here. It is not in a fresh clone and it is not on CI, so this",
        "line works for you and quietly stops working for everybody else.",
        "The other half of the squash-merge story: the branch commit survives",
        "locally long after the one that landed replaced it.",
    ],
}

CONFIG_HINT = (
    "note: blame.ignoreRevsFile is not set in this repository, so git blame on "
    "the command line is not reading {path} at all.\n"
    "  The web UI honours the file by itself; local git does not. Fix it here "
    "with:  blamecheck --configure"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blamecheck",
        description=(
            "Check that the commits in .git-blame-ignore-revs are real, "
            "reachable, and actually being ignored."
        ),
        epilog=(
            "Exit codes: 0 the file is doing its job, 1 at least one revision "
            "is not, 2 the check could not run."
        ),
    )
    parser.add_argument(
        "--file",
        default=DEFAULT_FILE,
        metavar="FILE",
        help=f"the ignore file to check (default: {DEFAULT_FILE} at the repo root)",
    )
    parser.add_argument(
        "--ref",
        default="HEAD",
        metavar="REV",
        help=(
            "check reachability against REV instead of HEAD. Use "
            "--ref origin/main to ask the question a fresh clone would ask"
        ),
    )
    parser.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="REV",
        help=(
            "append REV to the file as a full object name, with its subject on "
            "a comment line above it. Repeatable. Does not check the rest of "
            "the file"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every revision in the file with its status, then exit",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help=(
            "set blame.ignoreRevsFile for this repository, so that git blame "
            "on the command line reads the file. Then exit"
        ),
    )
    parser.add_argument(
        "-C",
        dest="directory",
        default=".",
        metavar="DIR",
        help="run as if started in DIR (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print nothing; report only through the exit code",
    )
    return parser


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _describe_commit(finding: Finding) -> str:
    commit = finding.commit
    if commit is None:
        return "—"
    return f"{commit.subject}  ({commit.author}, {commit.date[:10]})"


def _print_report(report: Report, out) -> None:
    errors = report.errors
    warnings = report.warnings
    if errors:
        summary = _plural(len(errors), "problem")
    elif warnings:
        summary = f"clean, {_plural(len(warnings), 'duplicate')}"
    else:
        summary = "clean"

    print(
        f"{report.path} — {_plural(report.revs, 'rev')}, "
        f"checked against {report.ref}, {summary}",
        file=out,
    )

    if any(f.status == MALFORMED for f in errors):
        print(file=out)
        print(BLAME_IS_DOWN, file=out)

    # The long explanation of each kind of failure earns its space once. A file
    # with nine stale revisions does not need the squash-merge story nine
    # times; it needs nine line numbers it can act on.
    explained: set[str] = set()

    for finding in errors + warnings:
        print(file=out)
        print(f"  line {finding.lineno}   {finding.text}", file=out)

        if finding.status == DUPLICATE:
            print(
                f"    duplicate — already named on line {finding.first_seen}",
                file=out,
            )
        else:
            headline = HEADLINE[finding.status].format(ref=report.ref)
            print(f"    {finding.status} — {headline}", file=out)

        if finding.commit is not None:
            print(f"    {_describe_commit(finding)}", file=out)

        if finding.status in DETAIL and finding.status not in explained:
            explained.add(finding.status)
            print(file=out)
            for line in DETAIL[finding.status]:
                print(f"    {line}", file=out)


def _print_list(report: Report, out) -> None:
    print(
        f"{report.path} — {_plural(report.revs, 'rev')}, "
        f"checked against {report.ref}",
        file=out,
    )
    if not report.findings:
        print(file=out)
        print("  Nothing listed. An empty ignore file is a fine thing to have:", file=out)
        print(
            "  it keeps a global blame.ignoreRevsFile setting from breaking blame here.",
            file=out,
        )
        return
    print(file=out)
    for finding in report.findings:
        print(
            f"  line {finding.lineno:<4} {finding.status:<12} "
            f"{_abbrev(finding.text):<13} {_describe_commit(finding)}".rstrip(),
            file=out,
        )


def _abbrev(text: str, width: int = 12) -> str:
    """Enough of the name to recognise, with the cut marked.

    Marked, because a short sha that git rejects and the first characters of
    the full one it should have been look identical otherwise, and those two
    lines sitting one above the other is the most likely reason you ran --list.
    """
    return text if len(text) <= width else text[:width] + "…"


def _report_to_dict(report: Report) -> dict:
    return {
        "path": report.path,
        "ref": report.ref,
        "revs": report.revs,
        "ok": report.ok,
        "findings": [
            {
                "line": f.lineno,
                "name": f.text,
                "status": f.status,
                "first_seen": f.first_seen,
                "commit": (
                    None
                    if f.commit is None
                    else {
                        "sha": f.commit.sha,
                        "author": f.commit.author,
                        "date": f.commit.date,
                        "subject": f.commit.subject,
                    }
                ),
            }
            for f in report.findings
        ],
    }


def _do_add(args, root: str, out) -> int:
    """Append revisions to the file as full object names."""
    path = args.file if os.path.isabs(args.file) else os.path.join(root, args.file)

    try:
        existing = read_file(root, args.file)
    except GitError as exc:
        if "no such file" not in str(exc):
            print(f"blamecheck: {exc}", file=sys.stderr)
            return EXIT_ERROR
        existing = ""

    hex_len = revsfile.HEX_LEN.get(
        gitcmd.object_format(args.directory), revsfile.DEFAULT_HEX_LEN
    )
    already = {
        line.name for line in revsfile.parse(existing, hex_len=hex_len) if line.name
    }

    to_append: list[str] = []
    messages: list[str] = []
    for rev in args.add:
        try:
            sha = gitcmd.resolve(rev, args.directory)
        except GitError:
            print(
                f"blamecheck: --add {rev}: no such revision in this repository.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        if sha in already or sha in {s for s in to_append}:
            messages.append(f"  {sha[:9]}  already in {args.file}, left alone")
            continue
        commit = gitcmd.describe([sha], args.directory).get(sha)
        to_append.append(sha)
        block = revsfile.render_entry(
            sha,
            subject=commit.subject if commit else None,
            date=commit.date if commit else None,
        )
        messages.append(f"  {sha[:9]}  {commit.subject if commit else ''}".rstrip())
        existing = _append_block(existing, block)

    if to_append:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(existing)

    if not args.quiet:
        added = (
            f"added {_plural(len(to_append), 'rev')}"
            if to_append
            else "nothing to add"
        )
        print(f"{args.file} — {added}", file=out)
        for message in messages:
            print(message, file=out)
    return EXIT_OK


def _append_block(text: str, block: str) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = sys.stdout

    if not os.path.isdir(args.directory):
        print(f"blamecheck: no such directory: {args.directory}", file=sys.stderr)
        return EXIT_ERROR

    try:
        root = gitcmd.repo_root(args.directory)
    except GitError as exc:
        print(f"blamecheck: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.configure:
        try:
            gitcmd.set_ignore_revs_config(args.file, args.directory)
        except GitError as exc:
            print(f"blamecheck: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if not args.quiet:
            print(f"blame.ignoreRevsFile = {args.file}", file=out)
            print(
                "  Set for this repository only. git blame here now reads the "
                "file the way the web UI already did.",
                file=out,
            )
        return EXIT_OK

    if args.add:
        return _do_add(args, root, out)

    # A shallow clone has neither the objects nor the history to answer any of
    # the questions this tool asks, and would report a file full of perfectly
    # good revisions as missing. Refusing is the only honest option.
    try:
        if gitcmd.is_shallow(args.directory):
            print(
                "blamecheck: this is a shallow clone, so 'missing' and "
                "'unreachable' cannot be told from 'not fetched'.",
                file=sys.stderr,
            )
            print(
                "  actions/checkout needs fetch-depth: 0 before this check "
                "means anything.",
                file=sys.stderr,
            )
            return EXIT_ERROR
    except GitError as exc:  # pragma: no cover - very old git
        print(f"blamecheck: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        gitcmd.resolve(args.ref, args.directory)
    except GitError:
        print(
            f"blamecheck: --ref {args.ref}: no such revision in this repository.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    try:
        report = check(args.directory, path=args.file, ref=args.ref)
    except GitError as exc:
        print(f"blamecheck: {exc}", file=sys.stderr)
        if "no such file" in str(exc):
            print(
                "  Nothing was checked. If this repository has no formatting "
                "commits to ignore yet, an empty file is the usual thing to "
                "commit, and it passes.",
                file=sys.stderr,
            )
        return EXIT_ERROR

    if args.quiet:
        return EXIT_OK if report.ok else EXIT_PROBLEMS

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2), file=out)
        return EXIT_OK if report.ok else EXIT_PROBLEMS

    if args.list:
        _print_list(report, out)
        return EXIT_OK

    _print_report(report, out)

    # Worth saying once, at the end, only when there is nothing more urgent to
    # say: a file that is in perfect order is still doing nothing for anybody
    # whose git has not been told to read it.
    if report.ok and gitcmd.ignore_revs_config(args.directory) is None:
        # stdout is block-buffered when it isn't a terminal, and stderr is not,
        # so without this the note lands above the report it is a footnote to
        # the moment anybody pipes the output anywhere.
        out.flush()
        print(file=sys.stderr)
        print(CONFIG_HINT.format(path=args.file), file=sys.stderr)

    return EXIT_OK if report.ok else EXIT_PROBLEMS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
