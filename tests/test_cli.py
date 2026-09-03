"""Exit codes, because that is the whole interface when this runs in CI."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from blamecheck.cli import main
from blamecheck.revsfile import parse

ABSENT = "deadbeef" * 5


def run(repo, *args, capsys=None):
    code = main(["-C", repo.path, *args])
    if capsys is None:
        return code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_clean_file_exits_zero(formatted, capsys):
    formatted.ignore_revs(formatted.fmt)
    code, out, _ = run(formatted, capsys=capsys)
    assert code == 0
    assert "1 rev, checked against HEAD, clean" in out


def test_problems_exit_one(formatted, capsys):
    formatted.ignore_revs(ABSENT)
    code, out, _ = run(formatted, capsys=capsys)
    assert code == 1
    assert "1 problem" in out
    assert "line 1" in out
    # The headline is what git does about it, not a restatement of the status.
    assert "blames as normal" in out


def test_missing_file_exits_two(formatted, capsys):
    code, out, err = run(formatted, capsys=capsys)
    assert code == 2
    assert "no such file" in err
    assert out == ""


def test_empty_file_exits_zero(formatted, capsys):
    """Deliberately not a 2. An empty ignore file cannot have rotted."""
    formatted.write(".git-blame-ignore-revs", "# nothing to ignore yet\n")
    code, out, _ = run(formatted, capsys=capsys)
    assert code == 0
    assert "0 revs" in out


def test_bad_ref_exits_two(formatted, capsys):
    formatted.ignore_revs(formatted.fmt)
    code, _, err = run(formatted, "--ref", "origin/nope", capsys=capsys)
    assert code == 2
    assert "no such revision" in err


def test_missing_directory_exits_two(formatted, capsys):
    code = main(["-C", formatted.path + "/nope"])
    assert code == 2
    assert "no such directory" in capsys.readouterr().err


def test_outside_a_repo_exits_two(tmp_path, capsys):
    code = main(["-C", str(tmp_path)])
    assert code == 2
    assert capsys.readouterr().err != ""


def test_shallow_clone_exits_two(formatted, tmp_path, capsys):
    """A shallow clone cannot tell 'missing' from 'not fetched'."""
    formatted.ignore_revs(formatted.fmt)
    formatted.commit("add the ignore file")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{formatted.path}", str(shallow)],
        check=True,
        capture_output=True,
    )
    code = main(["-C", str(shallow)])
    err = capsys.readouterr().err
    assert code == 2
    assert "shallow" in err and "fetch-depth: 0" in err


def test_quiet_prints_nothing(formatted, capsys):
    formatted.ignore_revs(ABSENT)
    code, out, err = run(formatted, "-q", capsys=capsys)
    assert code == 1
    assert out == "" and err == ""


def test_json(formatted, capsys):
    formatted.ignore_revs(formatted.fmt, ABSENT)
    code, out, _ = run(formatted, "--json", capsys=capsys)
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["revs"] == 2
    assert [f["status"] for f in payload["findings"]] == ["ok", "missing"]
    assert payload["findings"][0]["commit"]["subject"] == "reformat: shout"
    assert payload["findings"][1]["commit"] is None


def test_list_shows_every_rev_and_always_exits_zero(formatted, capsys):
    formatted.ignore_revs(formatted.fmt, ABSENT)
    code, out, _ = run(formatted, "--list", capsys=capsys)
    assert code == 0, "--list reports, it does not gate"
    assert "reformat: shout" in out
    assert "missing" in out


def test_file_option(formatted, capsys):
    formatted.write("other-revs", f"{formatted.fmt}\n")
    code, out, _ = run(formatted, "--file", "other-revs", capsys=capsys)
    assert code == 0
    assert "other-revs — 1 rev" in out


def test_config_hint_only_when_unset(formatted, capsys):
    formatted.ignore_revs(formatted.fmt)
    _, _, err = run(formatted, capsys=capsys)
    assert "blame.ignoreRevsFile is not set" in err

    formatted.git("config", "blame.ignoreRevsFile", ".git-blame-ignore-revs")
    _, _, err = run(formatted, capsys=capsys)
    assert err == ""


def test_config_hint_is_not_shown_over_a_real_problem(formatted, capsys):
    formatted.ignore_revs(ABSENT)
    _, _, err = run(formatted, capsys=capsys)
    assert err == "", "one thing at a time: fix the file, then the config"


def test_configure_sets_the_repo_local_config(formatted, capsys):
    code, out, _ = run(formatted, "--configure", capsys=capsys)
    assert code == 0
    assert "blame.ignoreRevsFile = .git-blame-ignore-revs" in out
    assert (
        formatted.git("config", "--get", "blame.ignoreRevsFile").strip()
        == ".git-blame-ignore-revs"
    )


# --- --add ------------------------------------------------------------------


def test_add_writes_a_full_sha_and_a_subject(formatted, capsys):
    code, out, _ = run(formatted, "--add", "HEAD", capsys=capsys)
    assert code == 0
    first, second = formatted.read(".git-blame-ignore-revs").splitlines()
    assert first.startswith("# reformat: shout (")
    assert second == formatted.fmt
    assert "added 1 rev" in out


def test_add_creates_the_file(formatted):
    run(formatted, "--add", "HEAD")
    assert parse(formatted.read(".git-blame-ignore-revs"))[0].name == formatted.fmt


def test_added_lines_survive_a_round_trip_through_the_checker(formatted, capsys):
    """Whatever --add writes, the checker and git both have to accept."""
    run(formatted, "--add", "HEAD", "--add", formatted.base)
    assert formatted.blame_exit() == 0
    code, out, _ = run(formatted, capsys=capsys)
    assert code == 0
    assert "2 revs" in out


def test_add_refuses_a_revision_that_does_not_exist(formatted, capsys):
    code, _, err = run(formatted, "--add", "no-such-branch", capsys=capsys)
    assert code == 2
    assert "no such revision" in err
    # And it did not leave behind a file it could not fill.
    assert not os.path.exists(formatted.path + "/.git-blame-ignore-revs")


def test_add_is_idempotent(formatted, capsys):
    run(formatted, "--add", "HEAD")
    before = formatted.read(".git-blame-ignore-revs")
    code, out, _ = run(formatted, "--add", "HEAD", capsys=capsys)
    assert code == 0
    assert formatted.read(".git-blame-ignore-revs") == before
    assert "already in" in out


def test_add_resolves_a_short_sha_to_a_full_one(formatted):
    """The whole point: what goes in the file is never what you typed."""
    run(formatted, "--add", formatted.fmt[:8])
    assert parse(formatted.read(".git-blame-ignore-revs"))[0].name == formatted.fmt


def test_add_appends_without_a_stray_blank_line(formatted):
    formatted.write(".git-blame-ignore-revs", f"{formatted.base}")  # no newline
    run(formatted, "--add", "HEAD")
    names = [line.name for line in parse(formatted.read(".git-blame-ignore-revs"))]
    assert names == [formatted.base, formatted.fmt]


# A failed write is not a failed check. These all have to be 2, never 1 --
# a CI script gating on 1 would read "the file is read-only" as "the file has
# rotted", which is the one wrong answer this tool must not give.


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write a read-only file")
def test_add_to_an_unwritable_file_exits_two(formatted, capsys):
    formatted.ignore_revs(formatted.base)
    os.chmod(formatted.path + "/.git-blame-ignore-revs", 0o444)
    before = formatted.read(".git-blame-ignore-revs")

    code, out, err = run(formatted, "--add", "HEAD", capsys=capsys)

    assert code == 2
    assert "could not write .git-blame-ignore-revs" in err
    assert "Permission denied" in err
    assert "nothing on disk changed" in err
    assert out == ""
    # And it really is as it was.
    assert formatted.read(".git-blame-ignore-revs") == before


def test_add_under_a_directory_that_does_not_exist_exits_two(formatted, capsys):
    code, out, err = run(formatted, "--file", "nope/revs", "--add", "HEAD", capsys=capsys)
    assert code == 2
    assert "could not write nope/revs" in err
    assert "nothing on disk changed" in err
    assert out == ""


def test_add_that_fails_mid_write_says_the_file_is_damaged(formatted, capsys, monkeypatch):
    """``w`` truncates before it writes, so this case cannot claim otherwise."""
    formatted.ignore_revs(formatted.base)
    target = formatted.path + "/.git-blame-ignore-revs"
    real_open = open

    class FullDisk:
        def write(self, _text):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_open(file, *args, **kwargs):
        if str(file) == target and "w" in str(args[0] if args else kwargs.get("mode", "")):
            return FullDisk()
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    code, out, err = run(formatted, "--add", "HEAD", capsys=capsys)

    assert code == 2
    assert "No space left on device" in err
    assert "is now incomplete" in err
    assert "git checkout -- .git-blame-ignore-revs" in err
    assert out == ""
