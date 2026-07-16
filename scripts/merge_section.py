#!/usr/bin/env python3
"""Idempotently replace-or-append one Markdown ``##`` section in a document.

Reference implementation for the markdown-idempotent-section-merge skill:

- Heading scans are fence-aware: lines inside ``` or ~~~ fenced code blocks
  never count as headings or section boundaries.
- A section boundary is the next heading of level 1 or 2 (``#`` or ``##``
  followed by space/tab or end of line, up to 3 leading spaces). ``###``
  subheadings stay inside the section; an H1 ends it — a part boundary must
  never be swallowed into a replace.
- The merged block must contain exactly one H1/H2-level heading: its own
  first line, a plain column-0 ``## Heading``.
- Malformed input is refused (exit 2) instead of guessed at: duplicate
  copies of the heading in the target, extra headings in the block, an
  unclosed code fence in either, CR-only line endings, or a possible setext
  heading (``===`` / ``---`` underline) inside the replaced span.
- Applying the same merge twice leaves the file byte-identical
  (apply-twice-diff-zero). The target's line-ending style (LF or CRLF) and
  UTF-8 BOM are preserved; when only mixed line endings need normalizing,
  the action is reported as ``normalized``, not hidden.

Usage:

    python merge_section.py TARGET BLOCK [--check]

The BLOCK file's first line is the exact ``## Heading`` line of the section
to replace (when TARGET already contains it outside code fences) or append
(when it does not). A missing TARGET is treated as an empty document, so
the section is appended and the file is created.

Exit codes: 0 = success. With --check (write nothing): 0 = TARGET is
already canonical, 1 = a merge (or line-ending normalization) would change
it. 2 = usage or validation error.
"""

import argparse
import re
import sys
from pathlib import Path

# A fence delimiter line: up to 3 leading spaces, then a run of 3+ backticks
# or 3+ tildes. Four or more leading spaces would be an indented code block,
# not a fence.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

# A section boundary: a heading of level 1 or 2 — up to 3 leading spaces,
# then "#" or "##" followed by space, tab, or end of line (CommonMark's ATX
# rule, so a "##hashtag" paragraph line is not a boundary). This hardens the
# folk form ``^##[^#]``: it still excludes ``###``, and it additionally
# treats an H1 as a boundary instead of silently replacing across it.
_BOUNDARY_RE = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")

# The canonical block's own heading: a plain column-0 H2.
_H2_RE = re.compile(r"^##(?:[ \t]|$)")

# A possible setext underline: a run of "=" or "-" alone on a line. Under a
# paragraph line this is a real heading that the boundary scan above cannot
# see, so the merge refuses to replace across one.
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")

_BOM = b"\xef\xbb\xbf"


class MergeError(ValueError):
    """Raised when the block or the target violates a merge invariant."""


def _fence_scan(lines):
    """Return ``(states, open_at_end)``.

    ``states`` maps each line to True when it belongs to a fenced code
    block; both delimiter lines count as inside. A backtick fence whose info
    string contains a backtick does not open (CommonMark). A fence closes
    only on a run of the same character, at least as long as the opener,
    with nothing else on the line; an unclosed fence runs to the end of the
    document (CommonMark behaviour), which ``open_at_end`` reports.
    """
    states = []
    open_char = ""
    open_len = 0
    for line in lines:
        match = _FENCE_RE.match(line)
        if not open_char:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                open_char = match.group(1)[0]
                open_len = len(match.group(1))
                states.append(True)
            else:
                states.append(False)
            continue
        states.append(True)
        if (
            match
            and match.group(1)[0] == open_char
            and len(match.group(1)) >= open_len
            and not match.group(2).strip()
        ):
            open_char = ""
            open_len = 0
    return states, bool(open_char)


def fence_states(lines):
    """Map each line to True when it belongs to a fenced code block."""
    return _fence_scan(lines)[0]


def boundary_indices(lines):
    """Indices of section-boundary headings (H1/H2), ignoring code fences."""
    states = fence_states(lines)
    return [
        i
        for i, line in enumerate(lines)
        if not states[i] and _BOUNDARY_RE.match(line)
    ]


def heading_occurrences(lines, heading):
    """Indices of non-fenced lines that equal HEADING (trailing space aside)."""
    states = fence_states(lines)
    return [
        i
        for i, line in enumerate(lines)
        if not states[i] and line.rstrip() == heading
    ]


def _split_lines(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # drop the empty tail produced by a trailing newline
    return lines


def _strip_trailing_blank(lines):
    while lines and not lines[-1].strip():
        lines.pop()


def validate_block(block_lines):
    """Return the block's heading line after checking the merge invariants."""
    if not block_lines or not _H2_RE.match(block_lines[0]):
        raise MergeError("block must start with an H2 heading line ('## ...')")
    if boundary_indices(block_lines) != [0]:
        raise MergeError(
            "block must contain exactly one H1/H2-level heading outside "
            "code fences: its own first line"
        )
    if _fence_scan(block_lines)[1]:
        # An unclosed fence in the block would swallow whatever follows the
        # merged section in the target — refuse instead of corrupting.
        raise MergeError("block ends inside an unclosed code fence")
    return block_lines[0].rstrip()


def _reject_setext_in_span(lines, start, end):
    """Refuse to replace across a possible setext heading.

    A ``===`` or ``---`` run directly under a non-blank line is (or may be)
    a real heading that ``_BOUNDARY_RE`` cannot see. Replacing across it
    would delete a section boundary without any error, so stop and report.
    """
    states = fence_states(lines)
    for index in range(start + 1, end):
        if states[index] or not _SETEXT_RE.match(lines[index]):
            continue
        if index - 1 <= start or states[index - 1]:
            continue
        previous = lines[index - 1]
        if not previous.strip() or _BOUNDARY_RE.match(previous):
            continue
        raise MergeError(
            "possible setext heading ('===' or '---' underline) inside the "
            "section at line %d; convert it to an ATX heading or use fixed "
            "markers before merging" % (index + 1)
        )


def merge(document_text, block_text):
    """Merge BLOCK into DOCUMENT (both LF-normalized text).

    Returns ``(new_text, action)`` where action is 'replaced', 'appended',
    or 'unchanged'. new_text always ends with exactly one newline.
    """
    doc_lines = _split_lines(document_text)
    block_lines = _split_lines(block_text)
    _strip_trailing_blank(block_lines)
    heading = validate_block(block_lines)
    block_lines[0] = heading  # heading is written without trailing spaces

    if _fence_scan(doc_lines)[1]:
        # CommonMark runs an unclosed fence to EOF, so the section would
        # extend to EOF and a replace would rewrite the whole visually
        # swallowed tail. Malformed input: stop and report instead.
        raise MergeError(
            "target ends inside an unclosed code fence; close it before merging"
        )

    occurrences = heading_occurrences(doc_lines, heading)
    if len(occurrences) > 1:
        raise MergeError(
            "target contains %d copies of '%s' outside code fences; "
            "deduplicate it before merging" % (len(occurrences), heading)
        )

    if occurrences:
        start = occurrences[0]
        end = len(doc_lines)
        for index in boundary_indices(doc_lines):
            if index > start:
                end = index
                break
        _reject_setext_in_span(doc_lines, start, end)
        new_span = list(block_lines)
        if end < len(doc_lines):
            # Exactly one blank line between this section and the next one,
            # so a second run reproduces the same bytes.
            new_span.append("")
        new_lines = doc_lines[:start] + new_span + doc_lines[end:]
        action = "replaced"
    else:
        new_lines = list(doc_lines)
        _strip_trailing_blank(new_lines)
        if new_lines:
            new_lines.append("")  # one blank line before the appended section
        new_lines.extend(block_lines)
        action = "appended"

    new_text = "\n".join(new_lines) + "\n"
    if new_text == document_text:
        action = "unchanged"
    return new_text, action


def _decode(raw, what):
    text = raw[len(_BOM):].decode("utf-8") if raw.startswith(_BOM) else raw.decode("utf-8")
    if re.search(r"\r(?!\n)", text):
        # A lone CR is invisible to LF/CRLF handling and would break
        # apply-twice-diff-zero (the second run would still change bytes).
        raise MergeError(
            "%s contains CR line endings that are not part of CRLF; "
            "convert the file to LF or CRLF before merging" % what
        )
    return text


def merge_file(target, block, write=True):
    """Merge BLOCK file into TARGET file. Returns ``(changed, action)``.

    Action is 'replaced', 'appended', 'normalized' (content already
    canonical, only mixed line endings or a missing final newline were
    normalized), or 'unchanged'. The target's line-ending style and UTF-8
    BOM are preserved so that a second run is byte-identical. A file that
    mixes CRLF and LF is treated as CRLF (any CRLF present selects CRLF)
    and is stable from the second run on.
    """
    raw = target.read_bytes() if target.exists() else b""
    has_bom = raw.startswith(_BOM)
    text = _decode(raw, "target")
    eol = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n")

    block_text = _decode(block.read_bytes(), "block").replace("\r\n", "\n")

    merged, action = merge(normalized, block_text)
    out = merged if eol == "\n" else merged.replace("\n", eol)
    out_bytes = (_BOM if has_bom else b"") + out.encode("utf-8")
    changed = out_bytes != raw
    if not changed:
        action = "unchanged"
    else:
        if action == "unchanged":
            action = "normalized"
        if write:
            target.write_bytes(out_bytes)
    return changed, action


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Idempotently replace-or-append one Markdown '##' section."
    )
    parser.add_argument("target", type=Path, help="Markdown file to update")
    parser.add_argument(
        "block",
        type=Path,
        help="file whose first line is the '## ...' heading of the section",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether a merge would change TARGET; write nothing",
    )
    args = parser.parse_args(argv)

    try:
        changed, action = merge_file(args.target, args.block, write=not args.check)
    except (MergeError, OSError, UnicodeDecodeError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2

    if args.check:
        if changed:
            verb = {
                "replaced": "would-replace",
                "appended": "would-append",
                "normalized": "would-normalize",
            }.get(action, "would-change")
            print("%s: %s" % (verb, args.target))
            return 1
        print("up-to-date: %s" % args.target)
        return 0

    print("%s: %s" % (action, args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
