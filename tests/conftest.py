from __future__ import annotations

import subprocess

import pytest


class Repo:
    """A throwaway git repo you can drive from a test."""

    def __init__(self, path):
        self.path = str(path)

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            check=check,
            text=True,
        )
        return proc.stdout

    def write(self, name: str, content: str) -> None:
        path = f"{self.path}/{name}"
        parent = path.rsplit("/", 1)[0]
        subprocess.run(["mkdir", "-p", parent], check=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def read(self, name: str) -> str:
        with open(f"{self.path}/{name}", encoding="utf-8") as handle:
            return handle.read()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def commit_file(self, name: str, content: str, message: str) -> str:
        self.write(name, content)
        return self.commit(message)

    def rev(self, name: str) -> str:
        return self.git("rev-parse", name).strip()

    def ignore_revs(self, *lines: str) -> None:
        self.write(".git-blame-ignore-revs", "".join(f"{line}\n" for line in lines))

    def blame_exit(self, path: str = "f.txt", ignore: str = ".git-blame-ignore-revs") -> int:
        """What real git does with the current ignore file. 128 means it refused."""
        proc = subprocess.run(
            ["git", "blame", f"--ignore-revs-file={ignore}", path],
            cwd=self.path,
            capture_output=True,
        )
        return proc.returncode


@pytest.fixture
def repo(tmp_path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir()
    r = Repo(path)
    r.git("init", "-q", "-b", "main")
    r.git("config", "user.name", "Test Person")
    r.git("config", "user.email", "test@example.invalid")
    r.git("config", "commit.gpgsign", "false")
    return r


@pytest.fixture
def formatted(repo) -> Repo:
    """A repo with one real commit and one formatting commit on top.

    ``repo.base`` is the original, ``repo.fmt`` is the reformat -- the commit a
    real ignore file would name.
    """
    repo.base = repo.commit_file("f.txt", "a\nb\nc\n", "add f")
    repo.fmt = repo.commit_file("f.txt", "A\nB\nC\n", "reformat: shout")
    return repo
