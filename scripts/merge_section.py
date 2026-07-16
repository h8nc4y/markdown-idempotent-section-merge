#!/usr/bin/env python3
"""Idempotently replace-or-append one Markdown ``##`` section in a document.

Reference implementation for the markdown-idempotent-section-merge skill:

- Heading scans are fence-aware: lines inside ``` or ~~~ fenced code blocks
  never count as headings or section boundaries.
- A section boundary is the next H2-level heading only (``^##`` not followed
  by another ``#``), so ``###`` subheadings stay inside the section.
- The merged block must contain exactly one H2 heading: its own first line.
- Applying the same merge twice leaves the file byte-identical
  (apply-twice-diff-zero). The target's line-ending style (LF or CRLF) and
  UTF-8 BOM are preserved.

Usage:

    python merge_section.py TARGET BLOCK [--check]

The BLOCK file's first line is the exact ``## Heading`` line of the section
to replace (when TARGET already contains it outside code fences) or append
(when it does not). A missing TARGET is treated as an empty document, so the
section is appended and the file is created.

Exit codes: 0 = success. With --check (write nothing): 0 = TARGET is already
canonical, 1 = a merge would change it. 2 = usage or validation error.
"""

import argparse
import re
import sys
from pathlib import Path

# A fence delimiter line: up to 3 leading spaces, then a run of 3+ backticks
# or 3+ tildes. Four or more leading spaces would be an indented code block,
# not a fence.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

# An H2 boundary: "##" at column 0 not followed by a third "#". This is the
# grep-style ``^##[^#]`` written as a lookahead so a bare "##" line counts.
_H2_RE = re.compile(r"^##(?!#)")

_BOM = b"\xef\xbb\xbf"


class MergeError(ValueError):
    """Raised when the block or the target violates a merge invariant."""


def fence_states(lines):
    """Map each line to True when it belongs to a fenced code block.

    Both delimiter lines count as inside the block. A fence closes only on a
    run of the same character, at least as long as the opener, with nothing
    else on the line; an unclosed fence runs to the end of the document
    (CommonMark behaviour).
    """
    states = []
    open_char = ""
    open_len = 0
    for line in lines:
        match = _FENCE_RE.match(line)
        if not open_char:
            if match:
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
    return states


def h2_indices(lines):
    """Indices of H2 heading lines, ignoring anything inside code fences."""
    states = fence_states(lines)
    return [
        i for i, line in enumerate(lines) if not states[i] and _H2_RE.match(line)
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
    h2s = h2_indices(block_lines)
    if h2s != [0]:
        raise MergeError(
            "block must contain exactly one H2 heading outside code fences; "
            "found %d" % len(h2s)
        )
    return block_lines[0].rstrip()


def merge(document_text, block_text):
    """Merge BLOCK into DOCUMENT (both LF-normalized text).

    Returns ``(new_text, action)`` where action is 'replaced', 'appended',
    or 'unchanged'. new_text always ends with exactly one newline.
    """
    doc_lines = _split_lines(document_text)
    block_lines = _split_lines(block_text)
    _strip_trailing_blank(block_lines)
    heading = validate_block(block_lines)

    occurrences = heading_occurrences(doc_lines, heading)
    if len(occurrences) > 1:
        raise MergeError(
            "target contains %d copies of '%s' outside code fences; "
            "deduplicate it before merging" % (len(occurrences), heading)
        )

    if occurrences:
        start = occurrences[0]
        end = len(doc_lines)
        for index in h2_indices(doc_lines):
            if index > start:
                end = index
                break
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


def merge_file(target, block, write=True):
    """Merge BLOCK file into TARGET file. Returns ``(changed, action)``.

    The target's line-ending style and UTF-8 BOM are preserved so that a
    second run is byte-identical. Files with mixed line endings are
    normalized to the detected style on the first write and are stable from
    the second run on.
    """
    raw = target.read_bytes() if target.exists() else b""
    has_bom = raw.startswith(_BOM)
    text = raw[len(_BOM):].decode("utf-8") if has_bom else raw.decode("utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n")

    block_raw = block.read_bytes()
    if block_raw.startswith(_BOM):
        block_raw = block_raw[len(_BOM):]
    block_text = block_raw.decode("utf-8").replace("\r\n", "\n")

    merged, action = merge(normalized, block_text)
    out = merged if eol == "\n" else merged.replace("\n", eol)
    out_bytes = (_BOM if has_bom else b"") + out.encode("utf-8")
    changed = out_bytes != raw
    if not changed:
        action = "unchanged"
    elif write:
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
            verb = "would-replace" if action == "replaced" else "would-append"
            print("%s: %s" % (verb, args.target))
            return 1
        print("up-to-date: %s" % args.target)
        return 0

    print("%s: %s" % (action, args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
