"""Each of the four failures, built for real rather than mocked."""

from __future__ import annotations

import pytest

from blamecheck.core import (
    DUPLICATE,
    MALFORMED,
    MISSING,
    NOT_A_COMMIT,
    OK,
    UNREACHABLE,
    check,
)
from blamecheck.gitcmd import GitError

ABSENT = "deadbeef" * 5


def statuses(repo, **kwargs):
    report = check(repo.path, **kwargs)
    return [f.status for f in report.findings]


def test_a_good_file_is_clean(formatted):
    formatted.ignore_revs("# the great reformatting", formatted.fmt)
    report = check(formatted.path)
    assert report.ok
    assert statuses(formatted) == [OK]
    assert report.findings[0].commit.subject == "reformat: shout"
    assert report.findings[0].lineno == 2


def test_an_empty_file_is_clean(formatted):
    """An empty ignore file is a real thing to commit, not a broken one."""
    formatted.write(".git-blame-ignore-revs", "")
    report = check(formatted.path)
    assert report.ok
    assert report.revs == 0


def test_missing_object(formatted):
    formatted.ignore_revs(ABSENT)
    report = check(formatted.path)
    assert not report.ok
    assert statuses(formatted) == [MISSING]
    # Nothing to describe, which is precisely why this one is hard by hand.
    assert report.findings[0].commit is None


def test_malformed_line(formatted):
    formatted.ignore_revs(formatted.fmt[:8])
    assert statuses(formatted) == [MALFORMED]
    # And git really would refuse to blame at all.
    assert formatted.blame_exit() == 128


def test_malformed_line_is_reported_with_the_text_as_written(formatted):
    formatted.ignore_revs("  not-a-sha  ")
    report = check(formatted.path)
    assert report.findings[0].text == "not-a-sha"


def test_tree_sha_is_not_a_commit(formatted):
    formatted.ignore_revs(formatted.rev("HEAD^{tree}"))
    assert statuses(formatted) == [NOT_A_COMMIT]
    # git accepts the line and quietly does nothing with it.
    assert formatted.blame_exit() == 0


def test_annotated_tag_is_peeled_not_rejected(formatted):
    formatted.git("tag", "-a", "v1", "-m", "v1", formatted.fmt)
    formatted.ignore_revs(formatted.rev("v1"))
    report = check(formatted.path)
    assert report.ok
    assert report.findings[0].commit.sha == formatted.fmt


def test_unreachable_commit(formatted):
    formatted.git("checkout", "-q", "-b", "side", formatted.base)
    side = formatted.commit_file("g.txt", "z\n", "work on a branch")
    formatted.git("checkout", "-q", "main")

    formatted.ignore_revs(side)
    report = check(formatted.path)
    assert not report.ok
    assert statuses(formatted) == [UNREACHABLE]
    # We can still say what it was, which is what you need to go and find the
    # commit that actually landed.
    assert report.findings[0].commit.subject == "work on a branch"

    # ...and against the branch it is on, it is fine.
    assert statuses(formatted, ref="side") == [OK]


def test_squash_merge_is_the_motivating_case(repo):
    """The branch commit stays in the clone; the one that landed is different.

    Reformat on a branch, squash-merge it, delete the branch. The sha you had
    open in a terminal while you wrote the ignore file is now a commit that
    only your clone has, and the file it went into looks entirely fine.
    """
    repo.commit_file("f.txt", "a\nb\nc\n", "add f")
    repo.git("checkout", "-q", "-b", "fmt")
    branch_fmt = repo.commit_file("f.txt", "A\nB\nC\n", "reformat on a branch")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--squash", "fmt")
    repo.git("commit", "-q", "-m", "reformat (#12)")
    repo.git("branch", "-D", "fmt")

    landed = repo.rev("HEAD")
    assert landed != branch_fmt

    # The sha you would have copied off the branch before it merged.
    repo.ignore_revs(branch_fmt)
    assert statuses(repo) == [UNREACHABLE]
    # git is perfectly happy with it, which is the entire problem.
    assert repo.blame_exit() == 0

    # The one that actually landed.
    repo.ignore_revs(landed)
    assert statuses(repo) == [OK]


def test_duplicate_is_a_warning_not_an_error(formatted):
    formatted.ignore_revs(formatted.fmt, formatted.fmt)
    report = check(formatted.path)
    assert report.ok, "a duplicate does not stop the file working"
    assert statuses(formatted) == [OK, DUPLICATE]
    assert report.warnings[0].first_seen == 1


def test_the_same_commit_by_sha_and_by_tag_is_a_duplicate(formatted):
    formatted.git("tag", "-a", "v1", "-m", "v1", formatted.fmt)
    formatted.ignore_revs(formatted.fmt, formatted.rev("v1"))
    assert statuses(formatted) == [OK, DUPLICATE]


def test_a_real_problem_beats_duplicate(formatted):
    """Two copies of a broken line report the breakage, not the tidiness."""
    formatted.ignore_revs(ABSENT, ABSENT)
    assert statuses(formatted) == [MISSING, MISSING]


def test_case_does_not_matter(formatted):
    formatted.ignore_revs(formatted.fmt.upper())
    assert statuses(formatted) == [OK]


def test_every_failure_in_one_file(formatted):
    formatted.git("checkout", "-q", "-b", "side", formatted.base)
    side = formatted.commit_file("g.txt", "z\n", "branch work")
    formatted.git("checkout", "-q", "main")
    formatted.ignore_revs(
        "# a comment",
        formatted.fmt,
        ABSENT,
        formatted.fmt[:8],
        formatted.rev("HEAD^{tree}"),
        side,
    )
    report = check(formatted.path)
    assert [(f.lineno, f.status) for f in report.findings] == [
        (2, OK),
        (3, MISSING),
        (4, MALFORMED),
        (5, NOT_A_COMMIT),
        (6, UNREACHABLE),
    ]
    assert len(report.errors) == 4


def test_no_file_is_an_error_not_a_pass(formatted):
    with pytest.raises(GitError, match="no such file"):
        check(formatted.path)


def test_a_directory_is_an_error(formatted):
    formatted.commit_file("src/keep.txt", "x\n", "add a directory")
    with pytest.raises(GitError, match="is a directory"):
        check(formatted.path, path="src")
