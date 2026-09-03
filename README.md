# blamecheck

Check that the commits in `.git-blame-ignore-revs` are real, reachable, and
actually being ignored.

You reformatted the repo, you put the commit in `.git-blame-ignore-revs`, and
`git blame` went back to showing you the person who wrote the line instead of
the person who ran the formatter. Then the file rotted, and nothing told you.

It rots because of one thing above all others: **a squash merge changes the
sha.** You copied the hash while the branch was open, the branch landed as a
different commit, and the line you committed now names an object that only your
clone has — or no object at all. git does not check. git parses hex and carries
whatever it finds into the ignore set, so a name for nothing is silently
ignored, forever, and blame is quietly wrong for everyone who comes after you.

```
$ blamecheck
.git-blame-ignore-revs — 5 revs, checked against HEAD, 2 problems

  line 5   8e926022653a75ef7671857a0d14249c29f9ee62
    unreachable — HEAD cannot reach it, so this works here and nowhere else
    reformat on a branch  (Sam Okafor, 2026-09-03)

    The commit is in this clone because the branch it was made on is
    still here. It is not in a fresh clone and it is not on CI, so this
    line works for you and quietly stops working for everybody else.
    The other half of the squash-merge story: the branch commit survives
    locally long after the one that landed replaced it.

  line 7   deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
    missing — no such object here, so git ignores the line and blames as normal
```

## Install

```
pip install git+https://github.com/committed-nightly/blamecheck
```

Python 3.10+. No dependencies — it shells out to `git`, which you already have.

## Usage

```
blamecheck [--file FILE] [--ref REV] [--list] [--add REV] [--configure]
```

With no arguments it checks `.git-blame-ignore-revs` at the root of the repo
against `HEAD`.

| | |
|---|---|
| `--file FILE` | check `FILE` instead of `.git-blame-ignore-revs`. |
| `--ref REV` | check reachability against `REV` instead of `HEAD`. `--ref origin/main` asks the question a fresh clone would ask. |
| `--list` | print every revision with its status and subject, then exit 0. Reports; does not gate. |
| `--add REV` | append `REV` to the file as a full object name. Repeatable. |
| `--configure` | set `blame.ignoreRevsFile` for this repository. |
| `-C DIR` | run as if started in `DIR`. |
| `--json` | machine-readable output. |
| `-q` | print nothing; report through the exit code only. |

Exit codes: **0** the file is doing its job, **1** at least one revision is
not, **2** the check could not run.

`2` is deliberately not `0`. A shallow clone, a file that isn't there, a
`--ref` that doesn't resolve — each of those means nobody looked, and "nobody
looked" reported as a green tick is worse than no check at all.

One case bends that rule on purpose: **an ignore file that exists and contains
no revisions is a pass.** An empty `.git-blame-ignore-revs` is a real and
useful thing to commit — it is what stops a global `blame.ignoreRevsFile`
setting from killing `git blame` in a repo that has never been reformatted —
and a file with nothing in it cannot have rotted.

## What it finds

Four failures, in the order they hurt, and each one was checked against git
rather than assumed:

**`malformed`** — git cannot parse the line, so **every `git blame` in the
repository exits 128** with `invalid object name` until it is fixed. Yours, your
colleagues', and the one your editor runs to draw the blame gutter. A short sha
does this. So does a note typed after the hash without a `#` in front of it:
`<sha> wip` is fatal, `<sha> # wip` is fine.

**`missing`** — a well-formed name for an object that isn't here. git never
looks it up, so the line is carried into the ignore set and does nothing. No
error, no warning, no visible difference. This is the squash-merge case, and it
is the reason the tool exists.

**`not-a-commit`** — the name resolves, but to a tree or a blob. Same silence.
Annotated tags are fine; git peels those.

**`unreachable`** — a real commit that `--ref` cannot reach. It is in your clone
because you still have the branch it was made on. It is not in a fresh clone and
it is not on CI, so the file works for you and has quietly stopped working for
everybody else.

And one thing that is only untidy, reported as a warning and not a failure:
**`duplicate`**, the same commit named twice, or once by sha and once by a tag
that points at it.

## Adding revisions

The file rots because people paste what their terminal showed them, and what a
terminal shows you is a short sha. `--add` resolves whatever you give it and
writes the full object name, with the subject on a comment line above so the
file is still readable in a year:

```
$ blamecheck --add HEAD
.git-blame-ignore-revs — added 1 rev
  8a6a3565a  run black over the tree

$ tail -2 .git-blame-ignore-revs
# run black over the tree (2026-09-03)
8a6a3565a63d55cdb506f7d0b78815079a854f29
```

It creates the file if it isn't there, and adding the same commit twice leaves
the file alone.

If it can't write — read-only file, parent directory that isn't there, full
disk — that is **exit 2**, not 1. Exit 1 is reserved for a revision that has
actually rotted, and a CI job gating on it should never be handed a
permissions problem wearing that code.

This is not a fixer. It will not rewrite a stale line for you, because it
cannot know which commit you meant — finding the one that actually landed is a
judgement call about your history, and a tool that guessed would be worse than
one that points at line 5 and stops.

## git blame is probably not reading your file

Worth knowing, because most people find out years late: **GitHub honours
`.git-blame-ignore-revs` on its own, and git on the command line does not.**
Locally the file does nothing at all until someone sets
`blame.ignoreRevsFile`, which is per-repository and per-clone, so every fresh
checkout starts ignoring it again.

```
$ blamecheck --configure
blame.ignoreRevsFile = .git-blame-ignore-revs
```

`blamecheck` says so at the end of a clean run when the setting is missing, and
otherwise keeps quiet.

## In CI

`fetch-depth: 0` is required. A shallow clone genuinely does not have the
objects, so every revision in the file would look `missing`; `blamecheck` says
so and exits 2 rather than failing you for it.

```yaml
name: blamecheck
on: pull_request

jobs:
  blamecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install git+https://github.com/committed-nightly/blamecheck
      - run: blamecheck
```

The check that matters most is the one that runs *after* a squash merge lands,
so running this on pushes to your main branch is worth at least as much as
running it on pull requests.

## Known limits

Worth reading before you rely on it.

**It does not judge whether a commit deserved to be ignored.** A revision that
quietly hides a real change — a formatting pass with one genuine edit smuggled
into it — is a commit like any other, and `blamecheck` reports it as `ok`.
Deciding what counts as "formatting only" means deciding whether turning `'x'`
into `"x"` is a formatting change, and the answer depends on your formatter, so
the tool doesn't pretend to know. Every check here is about whether a line does
what it claims, not about whether it should be there.

**`unreachable` is relative to `--ref`, and the default is `HEAD`.** On a
feature branch, a commit you made on that branch is reachable and reports `ok`
— correctly, for now. The rot appears the moment it squash-merges. Run
`--ref origin/main` if you want the answer a fresh clone would give you today.

**Duplicates are not deduplicated by content.** Two different commits that made
the same formatting change — a cherry-pick, a revert and reapply — are two
revisions, and both belong in the file. Only the same commit named twice is
reported.

**Partial clones are not detected.** A blobless or treeless clone is not
shallow, so `blamecheck` will run, and git will quietly fetch the commit objects
it needs. That is correct, just slower than you might expect the first time.

## Licence

MIT.
