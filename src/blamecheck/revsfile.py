"""Parse ``.git-blame-ignore-revs`` exactly the way git parses it.

This is the part that has to be right, because the whole tool is a claim about
what git will do with your file, and a parser that is merely close would make
that claim a lie in the cases that matter most.

git reads the file in ``oidset_parse_file_carefully``. Per line, after the
newline is stripped: leading whitespace is skipped, a full-length object name
is read, and then whatever remains must be blank or a comment. Everything else
is fatal -- not skipped, not warned about. One bad line and every ``git blame``
in the repository exits 128.

So, in practice, and each of these was checked against git 2.55 rather than
read off the documentation:

* a blank line is fine, and so is one that is only whitespace
* ``#`` starts a comment, with or without a space after it, and it may be
  indented
* the object name may be indented
* the name may be upper case
* the name may be followed by whitespace, and by a ``#`` comment
* the name may *not* be followed by anything else: ``<sha> wip`` is fatal,
  and so is ``<sha>\\tsome note``, because after the whitespace comes a word
  rather than a ``#``
* the name must be exactly the full length. 39 characters is fatal, 41 is
  fatal, and a short sha -- the single most common thing to paste in by hand --
  is fatal
* a well-formed name that does not exist is *not* fatal. git parses hex; it
  does not look the object up. That line is silently carried into the ignore
  set and silently does nothing, forever. Catching that is the reason this
  tool exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Length of a full object name, by hash. git will tell us which one the
# repository uses; sha256 repos are rare but they are not hypothetical, and
# hardcoding 40 would make this tool confidently wrong in one.
HEX_LEN = {"sha1": 40, "sha256": 64}
DEFAULT_HEX_LEN = 40

_NAME = re.compile(r"[0-9a-fA-F]+")


@dataclass(frozen=True)
class Line:
    """One line of the file that was meant to name a revision.

    ``name`` is the object name if the line is one git will accept, and None
    if it is one git will die on. Blank and comment lines never become a
    ``Line`` at all -- git skips them and so do we.
    """

    lineno: int
    raw: str
    name: str | None

    @property
    def malformed(self) -> bool:
        return self.name is None


def parse(text: str, *, hex_len: int = DEFAULT_HEX_LEN) -> list[Line]:
    """Split the file into the lines git will try to resolve.

    Order and line numbers are preserved, because "line 14" is the only useful
    thing to say to somebody about a file that is three hundred hashes long.
    """
    lines: list[Line] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(Line(lineno=lineno, raw=raw, name=_name_of(raw, hex_len)))
    return lines


def _name_of(raw: str, hex_len: int) -> str | None:
    """The object name on this line, or None if git would reject the line."""
    text = raw.lstrip()
    match = _NAME.match(text)
    if match is None:
        return None

    name = match.group(0)
    if len(name) != hex_len:
        # Too short is nearly always a hand-pasted short sha; too long is
        # nearly always a stray character on the end. git treats both the same
        # way, which is to refuse to run.
        return None

    rest = text[match.end() :].strip()
    if rest and not rest.startswith("#"):
        return None
    return name


def render_entry(sha: str, *, subject: str | None = None, date: str | None = None) -> str:
    """The text to append to the file for one commit, without a trailing newline.

    A comment line above the sha rather than beside it. Both are legal, but the
    file is read far more often by a person wondering "what is this hash" than
    by git, and a subject line that survives an 80-column terminal is worth
    more than one that trails off the right-hand edge.
    """
    if not subject:
        return sha
    stamp = f" ({date[:10]})" if date else ""
    return f"# {subject}{stamp}\n{sha}"
