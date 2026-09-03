"""The parser has to agree with git, not with the documentation.

Half of these are ordinary unit tests. The other half -- the ones at the
bottom -- hand the same lines to a real ``git blame`` and check that the two
of us disagree about nothing, because a parser that is merely close would make
this whole tool a confident lie in exactly the cases people install it for.
"""

from __future__ import annotations

import pytest

from blamecheck.revsfile import parse, render_entry

SHA = "22261293173c9a80be4d2b4231d8edbdf76ed7a2"

# (label, template, does git accept the line). The template is filled with a
# real sha so that the same table can be run against git itself further down.
LINES = [
    ("plain", "{sha}", True),
    ("upper case", "{SHA}", True),
    ("indented", "    {sha}", True),
    ("trailing spaces", "{sha}   ", True),
    ("trailing comment", "{sha} # why this is here", True),
    ("trailing comment, no space", "{sha}# why", True),
    ("tab then comment", "{sha}\t# why", True),
    ("short sha", "{short}", False),
    ("one character short", "{stunted}", False),
    ("one character long", "{sha}0", False),
    ("bare word", "definitely-not-a-sha", False),
    ("note without a hash", "{sha} wip do not merge", False),
    ("tab then a word", "{sha}\tstill wip", False),
]

IDS = [case[0] for case in LINES]


def fill(template: str, sha: str) -> str:
    return template.format(
        sha=sha, SHA=sha.upper(), short=sha[:8], stunted=sha[: len(sha) - 1]
    )


@pytest.mark.parametrize("label,template,valid", LINES, ids=IDS)
def test_line_is_read_as_git_reads_it(label, template, valid):
    line = fill(template, SHA)
    (parsed,) = parse(line + "\n")
    assert parsed.malformed is not valid
    if valid:
        # The name is returned exactly as written, case and all, because that
        # is the string git will hand to its object lookup.
        assert parsed.name is not None and parsed.name.lower() == SHA


def test_blanks_and_comments_are_not_revisions():
    text = f"# a comment\n\n   \n  # indented comment\n#nospace\n{SHA}\n"
    parsed = parse(text)
    assert [p.name for p in parsed] == [SHA]
    # The line number has to survive being the only survivor of six lines,
    # because "line 6" is the only useful thing to say about a long file.
    assert parsed[0].lineno == 6


def test_line_numbers_count_every_line():
    text = f"#\n{SHA}\n\nnonsense\n"
    assert [(p.lineno, p.name) for p in parse(text)] == [(2, SHA), (4, None)]


def test_empty_file_has_no_revisions():
    assert parse("") == []
    assert parse("\n\n# only comments\n") == []


def test_missing_trailing_newline_is_fine():
    assert [p.name for p in parse(SHA)] == [SHA]


def test_crlf_is_fine():
    assert [p.name for p in parse(f"{SHA}\r\n")] == [SHA]


def test_sha256_length():
    long_sha = "a" * 64
    assert parse(long_sha + "\n", hex_len=64)[0].name == long_sha
    # ...and the sha1 length is malformed in a sha256 repo.
    assert parse(SHA + "\n", hex_len=64)[0].name is None


def test_render_entry_puts_the_subject_above_the_sha():
    assert render_entry(SHA, subject="run black", date="2026-04-11T09:00:00Z") == (
        f"# run black (2026-04-11)\n{SHA}"
    )
    assert render_entry(SHA) == SHA


# --- the important half: check the parser against git itself ----------------


@pytest.mark.parametrize("label,template,valid", LINES, ids=IDS)
def test_parser_agrees_with_real_git(formatted, label, template, valid):
    """A line we call malformed is exactly a line git refuses to run on.

    git exits 128 with "invalid object name" on a line it cannot parse, and 0
    on one it can -- whether or not the object exists. Filling the template
    with a sha that really is in this repo keeps the case about parsing rather
    than about existence.
    """
    line = fill(template, formatted.fmt)
    formatted.ignore_revs(line)
    git_refused = formatted.blame_exit() == 128
    assert git_refused is not valid, (
        f"{label}: git {'refused' if git_refused else 'accepted'} {line!r}, "
        f"we called it {'fine' if valid else 'malformed'}"
    )


def test_a_well_formed_name_for_nothing_is_not_a_parse_error(formatted):
    """The whole premise, pinned: git is happy, and blame is silently wrong.

    If this ever fails because git started rejecting names it cannot resolve,
    then the ``missing`` check has stopped being the useful half of this tool
    and the README needs rewriting rather than the code.
    """
    formatted.ignore_revs("deadbeef" * 5)
    assert formatted.blame_exit() == 0
    assert parse("deadbeef" * 5 + "\n")[0].name is not None
