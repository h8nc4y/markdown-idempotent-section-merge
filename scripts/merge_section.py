#!/usr/bin/env python3
"""Idempotently replace-or-append one Markdown ``##`` section in a document.

Reference implementation for the markdown-idempotent-section-merge skill:

- Heading scans are literal-region-aware: lines inside ``` / ~~~ fenced code
  blocks or top-level CommonMark raw HTML blocks never count as headings or
  section boundaries.
- Exact document-leading YAML/TOML frontmatter is excluded from heading and
  fence scans; an unclosed recognized opener fails closed before writing.
- A section boundary is the next heading of level 1 or 2 (``#`` or ``##``
  followed by space/tab or end of line, up to 3 leading spaces). ``###``
  subheadings stay inside the section; an H1 ends it — a part boundary must
  never be swallowed into a replace.
- CommonMark block-whitespace decisions use ASCII space/tab only. Unicode
  whitespace remains heading/line content and never closes a fence or
  closing-hash sequence by implicit Python ``strip`` behavior.
- The merged block must contain exactly one H1/H2-level heading: its own
  first line, a plain column-0 ``## Heading``.
- Malformed input is refused (exit 2) instead of guessed at: duplicate or
  syntactically aliased copies of the heading in the target, a closing-hash
  or extra heading in the block, an unclosed code fence in either, unclosed
  explicit-end raw HTML block, unclosed leading YAML/TOML frontmatter,
  CR-only line endings, or a possible setext heading (``===`` / ``---``
  underline) inside the canonical block or replaced span.
- Applying the same merge twice leaves the file byte-identical
  (apply-twice-diff-zero). The target's line-ending style (LF or CRLF) and
  UTF-8 BOM are preserved; when only mixed line endings need normalizing,
  the action is reported as ``normalized``, not hidden.
- Changed content is flushed to a private same-directory temporary file and
  installed with one atomic replace. Target and block are read without
  following links; non-regular and multi-hard-link inputs are refused.
  Windows uses documented ``ReplaceFileW`` DACL/attribute/stream behavior;
  POSIX preserves owner/group, mode, and bounded extended attributes.
  The target identity, metadata, and bytes are rechecked immediately before
  commit. This is a best-effort conflict check, not compare-and-swap: writers
  needing strict lost-update prevention must serialize externally. Creation
  of a previously missing target uses an atomic no-replace commit.

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
import errno
import os
import re
import secrets
import stat
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

# CommonMarkのATX closing sequence。見出し本文と空白で区切られ、末尾まで
# unescaped ``#`` と空白だけが続く場合に限って本文から除外される。
_ATX_CLOSING_SEQUENCE_RE = re.compile(r"[ \t]+#+[ \t]*$")

# A possible setext underline: a run of "=" or "-" alone on a line. Under a
# paragraph line this is a real heading that the boundary scan above cannot
# see, so the merge refuses to replace across one.
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")

# Leading frontmatter is intentionally a small exact-line contract, not a
# YAML/TOML parser. Ambiguous unclosed input fails closed before any write.
_FRONTMATTER_CLOSERS = {
    "---": ("YAML", ("---", "...")),
    "+++": ("TOML", ("+++",)),
}

# CommonMark 0.31.2 raw HTML block rules. Types 1–5 have explicit end
# conditions; types 6/7 continue to the line before the next blank line.
# Keeping these regexes close to the spec makes the intentionally supported
# top-level profile auditable without importing a full Markdown parser.
_ASCII_CASE_INSENSITIVE = re.IGNORECASE | re.ASCII
_HTML_TYPE_1_START_RE = re.compile(
    r"^<(?:pre|script|style|textarea)(?:[ \t]|>|$)",
    _ASCII_CASE_INSENSITIVE,
)
_HTML_TYPE_1_END_RE = re.compile(
    r"</(?:pre|script|style|textarea)>",
    _ASCII_CASE_INSENSITIVE,
)
_HTML_TYPE_2_START_RE = re.compile(r"^<!--")
_HTML_TYPE_2_END_RE = re.compile(r"-->")
_HTML_TYPE_3_START_RE = re.compile(r"^<\?")
_HTML_TYPE_3_END_RE = re.compile(r"\?>")
_HTML_TYPE_4_START_RE = re.compile(r"^<![A-Za-z]")
_HTML_TYPE_4_END_RE = re.compile(r">")
_HTML_TYPE_5_START_RE = re.compile(r"^<!\[CDATA\[")
_HTML_TYPE_5_END_RE = re.compile(r"\]\]>")

_HTML_BLOCK_TAGS = (
    "address",
    "article",
    "aside",
    "base",
    "basefont",
    "blockquote",
    "body",
    "caption",
    "center",
    "col",
    "colgroup",
    "dd",
    "details",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "iframe",
    "legend",
    "li",
    "link",
    "main",
    "menu",
    "menuitem",
    "nav",
    "noframes",
    "ol",
    "optgroup",
    "option",
    "p",
    "param",
    "search",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "title",
    "tr",
    "track",
    "ul",
)
_HTML_TYPE_6_START_RE = re.compile(
    r"^</?(?:%s)(?:[ \t]|/?>|$)" % "|".join(_HTML_BLOCK_TAGS),
    _ASCII_CASE_INSENSITIVE,
)

# Type 7 uses CommonMark's ASCII tag/attribute grammar and must occupy its
# whole line. Literal-content open tags belong to type 1, while their closing
# tags may still start type 7 when encountered independently.
_HTML_TAG_NAME = r"[A-Za-z][A-Za-z0-9-]*"
_HTML_ATTRIBUTE_NAME = r"[A-Za-z_:][A-Za-z0-9:._-]*"
_HTML_UNQUOTED_VALUE = r"""[^"'=<>`\x00-\x20]+"""
_HTML_ATTRIBUTE_VALUE = (
    r'(?:(?:%s)|(?:\'[^\']*\')|(?:"[^"]*"))' % _HTML_UNQUOTED_VALUE
)
_HTML_ATTRIBUTE = (
    r"(?:[ \t]+%s(?:[ \t]*=[ \t]*%s)?)"
    % (_HTML_ATTRIBUTE_NAME, _HTML_ATTRIBUTE_VALUE)
)
_HTML_OPEN_TAG = (
    r"<(?!(?:pre|script|style|textarea)(?=[ \t/>]))"
    + _HTML_TAG_NAME
    + r"(?:"
    + _HTML_ATTRIBUTE
    + r")*[ \t]*/?>"
)
_HTML_CLOSE_TAG = r"</" + _HTML_TAG_NAME + r"[ \t]*>"
_HTML_TYPE_7_START_RE = re.compile(
    r"^(?:%s|%s)[ \t]*$" % (_HTML_OPEN_TAG, _HTML_CLOSE_TAG),
    _ASCII_CASE_INSENSITIVE,
)

_HTML_START_PATTERNS = (
    _HTML_TYPE_1_START_RE,
    _HTML_TYPE_2_START_RE,
    _HTML_TYPE_3_START_RE,
    _HTML_TYPE_4_START_RE,
    _HTML_TYPE_5_START_RE,
    _HTML_TYPE_6_START_RE,
    _HTML_TYPE_7_START_RE,
)
_HTML_EXPLICIT_END_PATTERNS = {
    1: _HTML_TYPE_1_END_RE,
    2: _HTML_TYPE_2_END_RE,
    3: _HTML_TYPE_3_END_RE,
    4: _HTML_TYPE_4_END_RE,
    5: _HTML_TYPE_5_END_RE,
}

# Type 7 alone cannot interrupt an open paragraph. These small block-start
# recognizers track only the top-level context this tool promises. A container
# or 4-space-indent context is marked unknown and a possible type 7 opener
# then fails closed instead of guessing.
_ATX_ANY_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_THEMATIC_BREAK_RE = re.compile(
    r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$"
)
_CONTAINER_START_RE = re.compile(
    r"^ {0,3}(?:>[ \t]?|[*+-](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$))"
)
_INDENTED_CODE_START_RE = re.compile(r"^(?: {4}|\t)")
_BLANK_RE = re.compile(r"^[ \t]*$")

_BOM = b"\xef\xbb\xbf"
_MAX_XATTR_COUNT = 256
_MAX_XATTR_BYTES = 1024 * 1024


class MergeError(ValueError):
    """Raised when the block or the target violates a merge invariant."""


class AtomicCommitError(MergeError):
    """Report a partial commit without discarding recovery files.

    ``committed`` is True, False, or None when the observed state cannot
    prove either outcome.
    """

    def __init__(self, message, committed=False, recovered=False, artifacts=()):
        super().__init__(message)
        self.committed = committed
        self.recovered = recovered
        self.artifacts = tuple(artifacts)


def _is_ascii_blank(text):
    """Return whether TEXT contains only CommonMark blank-line whitespace."""
    return _BLANK_RE.fullmatch(text) is not None


def _rstrip_ascii_whitespace(text):
    """Remove only ASCII space/tab used by CommonMark block grammar."""
    return text.rstrip(" \t")


def _frontmatter_scan(lines):
    """Return ``(states, unclosed_kind)`` for exact leading frontmatter.

    Only an exact opener on the first line is recognized. YAML closes with
    an exact ``---`` or ``...`` line; TOML closes with exact ``+++``.
    Delimiter lines themselves belong to frontmatter.
    """
    states = [False] * len(lines)
    if not lines or lines[0] not in _FRONTMATTER_CLOSERS:
        return states, None

    opener = lines[0]
    kind, closers = _FRONTMATTER_CLOSERS[opener]
    states[0] = True
    for index in range(1, len(lines)):
        states[index] = True
        if lines[index] in closers:
            return states, None
    return states, kind


def _html_block_start_type(candidate):
    """Return the first matching CommonMark HTML block type, or zero."""
    for block_type, pattern in enumerate(_HTML_START_PATTERNS, start=1):
        if pattern.match(candidate):
            return block_type
    return 0


def _first_unescaped_label_bracket(text, start=0):
    """Return ``(index, bracket)`` for the first unescaped ``[`` or ``]``."""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            # 行末 backslash も次行の改行をescapeし得るため、単独でも
            # 「無効」と断定せず bracket 未確定のまま走査を終える。
            escaped = True
            continue
        if char in "[]":
            return index, char
    return None


def _possible_single_line_reference_definition(line):
    """Return whether LINE has an unescaped link-label closer plus colon."""
    leading_spaces = len(line) - len(line.lstrip(" "))
    if leading_spaces > 3:
        return False
    candidate = line[leading_spaces:]
    if not candidate.startswith("["):
        return False

    # definition は paragraph finalize 時に除去されるため、直後の ``===`` が
    # setext にならない場合がある。完全構文は検証せず、少なくとも最初の
    # 未escape bracketが ``]:`` で閉じることだけを候補条件にする。
    bracket = _first_unescaped_label_bracket(candidate, start=1)
    if bracket is None:
        return False
    index, char = bracket
    return char == "]" and candidate[index + 1 :].startswith(":")


def _possible_multiline_reference_label_start(line):
    """Return whether LINE can start a link label that continues next line."""
    leading_spaces = len(line) - len(line.lstrip(" "))
    if leading_spaces > 3:
        return False
    candidate = line[leading_spaces:]
    if not candidate.startswith("["):
        return False

    # CommonMark label は未escape bracketを内包できない。開始 ``[`` 後に
    # それがまだ無ければ、行末 backslash の有無を問わず次行まで保留する。
    return _first_unescaped_label_bracket(candidate, start=1) is None


def _multiline_reference_label_continuation(line):
    """Return ``open``, ``definition``, or ``ordinary`` for a label line."""
    bracket = _first_unescaped_label_bracket(line)
    if bracket is None:
        return "open"

    index, char = bracket
    if char == "]" and line[index + 1 :].startswith(":"):
        return "definition"

    # 最初の未escape bracketが nested ``[``、または colon を伴わない
    # ``]`` なら reference definition にはなれず、通常段落へ確定する。
    return "ordinary"


def _markdown_region_scan(lines):
    """Return ``(ignored, fences, fence_open, html_open_type)``.

    The single pass makes fence and raw-HTML states mutually exclusive:
    delimiters inside the other literal region are data, never state changes.
    ``html_open_type`` can only be 1–5 because types 6/7 end normally at a
    blank line or EOF. A recognized but unclosed explicit-end HTML block is
    reported to callers so every write path can fail closed.
    """
    frontmatter_states, unclosed_kind = _frontmatter_scan(lines)
    if unclosed_kind is not None:
        expected = "'---' or '...'" if unclosed_kind == "YAML" else "'+++'"
        raise MergeError(
            "document starts with unclosed %s frontmatter; close it with an "
            "exact %s line before merging" % (unclosed_kind, expected)
        )

    ignored_states = list(frontmatter_states)
    fence_region_states = [False] * len(lines)
    open_fence_char = ""
    open_fence_len = 0
    open_html_type = 0
    paragraph_context = "block"
    paragraph_may_be_reference_only = False
    possible_reference_label_open = False

    for index, line in enumerate(lines):
        # frontmatter 内の fence/HTML delimiter は metadata の bytes であり、
        # 本文の状態機械へ一切持ち越さない。
        if frontmatter_states[index]:
            paragraph_context = "block"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
            continue

        fence_match = _FENCE_RE.match(line)
        if open_fence_char:
            ignored_states[index] = True
            fence_region_states[index] = True
            if (
                fence_match
                and fence_match.group(1)[0] == open_fence_char
                and len(fence_match.group(1)) >= open_fence_len
                and _is_ascii_blank(fence_match.group(2))
            ):
                open_fence_char = ""
                open_fence_len = 0
                paragraph_context = "block"
                paragraph_may_be_reference_only = False
                possible_reference_label_open = False
            continue

        if open_html_type:
            # type 6/7 は空行の「直前」で終了する。空行自体を ignored に
            # すると後続 paragraph context の開始点がずれるため先に閉じる。
            if open_html_type in (6, 7) and _BLANK_RE.match(line):
                open_html_type = 0
                paragraph_context = "block"
                paragraph_may_be_reference_only = False
                possible_reference_label_open = False
            else:
                ignored_states[index] = True
                if (
                    open_html_type <= 5
                    and _HTML_EXPLICIT_END_PATTERNS[open_html_type].search(line)
                ):
                    open_html_type = 0
                    paragraph_context = "block"
                    paragraph_may_be_reference_only = False
                    possible_reference_label_open = False
                continue

        if _BLANK_RE.match(line):
            paragraph_context = "block"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
            continue

        # CommonMark の block-start 優先順どおり、fence を HTML より先に
        # 判定する。backtick info string 内の backtick は opener ではない。
        if fence_match and not (
            fence_match.group(1)[0] == "`" and "`" in fence_match.group(2)
        ):
            open_fence_char = fence_match.group(1)[0]
            open_fence_len = len(fence_match.group(1))
            ignored_states[index] = True
            fence_region_states[index] = True
            paragraph_context = "block"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
            continue

        # HTML start condition は最大3個の ASCII space 後から評価する。
        # 4-space indentation では candidate が space 始まりとなり一致しない。
        candidate = line[min(len(line) - len(line.lstrip(" ")), 3) :]
        html_type = _html_block_start_type(candidate)
        if html_type == 7:
            if paragraph_context == "paragraph":
                # Complete tag 単独行でも段落を割り込めないため inline HTML。
                html_type = 0
            elif paragraph_context == "unknown":
                raise MergeError(
                    "ambiguous raw HTML block type 7 context at line %d; "
                    "separate the tag from a container or indented block with "
                    "a blank line before merging" % (index + 1)
                )
        if html_type:
            ignored_states[index] = True
            open_html_type = html_type
            paragraph_context = "block"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
            if (
                html_type <= 5
                and _HTML_EXPLICIT_END_PATTERNS[html_type].search(line)
            ):
                open_html_type = 0
            continue

        # Type 7 の paragraph-interruption 制約に必要な最小文脈だけを追う。
        # container/4-space code は完全解析せず unknown とし、次の type 7
        # 候補で安全側に拒否する。
        if _ATX_ANY_RE.match(line) or _THEMATIC_BREAK_RE.match(line):
            paragraph_context = "block"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
        elif paragraph_context == "paragraph" and _SETEXT_RE.match(line):
            if (
                paragraph_may_be_reference_only
                and not possible_reference_label_open
            ):
                raise MergeError(
                    "ambiguous setext context after a possible link reference "
                    "definition at line %d; add a blank line or use an ATX "
                    "heading before merging" % (index + 1)
                )
            # label が未クローズのまま underline に達した場合は definition
            # ではないため、通常の複数行 setext paragraph として閉じられる。
            paragraph_context = "block"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
        elif paragraph_context == "paragraph" and possible_reference_label_open:
            # 複数行 label の途中は container らしい記号も label bytes として
            # 現れ得る。最初の未escape bracketまでを見て状態だけ確定する。
            label_state = _multiline_reference_label_continuation(line)
            if label_state != "open":
                possible_reference_label_open = False
                if label_state == "ordinary":
                    paragraph_may_be_reference_only = False
        elif _CONTAINER_START_RE.match(line) or _INDENTED_CODE_START_RE.match(line):
            paragraph_context = "unknown"
            paragraph_may_be_reference_only = False
            possible_reference_label_open = False
        elif paragraph_context == "block":
            paragraph_context = "paragraph"
            possible_single_line_definition = (
                _possible_single_line_reference_definition(line)
            )
            possible_reference_label_open = (
                _possible_multiline_reference_label_start(line)
            )
            paragraph_may_be_reference_only = bool(
                possible_single_line_definition
                or possible_reference_label_open
            )

    return (
        ignored_states,
        fence_region_states,
        bool(open_fence_char),
        open_html_type if open_html_type <= 5 else 0,
    )


def fence_states(lines):
    """Map each line to True when it belongs to a fenced code block."""
    return _markdown_region_scan(lines)[1]


def boundary_indices(lines):
    """Indices of H1/H2 boundaries outside every supported literal region."""
    ignored_states = _markdown_region_scan(lines)[0]
    return [
        i
        for i, line in enumerate(lines)
        if not ignored_states[i] and _BOUNDARY_RE.match(line)
    ]


def heading_occurrences(lines, heading):
    """Indices exactly equal to HEADING outside literal regions."""
    ignored_states = _markdown_region_scan(lines)[0]
    return [
        i
        for i, line in enumerate(lines)
        if not ignored_states[i] and _rstrip_ascii_whitespace(line) == heading
    ]


def _indented_heading_occurrences(lines, heading, ignored_states):
    """Indices of 1–3 ASCII-space variants of the managed heading."""
    occurrences = []
    for index, line in enumerate(lines):
        if ignored_states[index]:
            continue

        # CommonMarkのATX見出しとして成立し得る1〜3 spaceだけを同一性候補にする。
        # 4+ space、tab、閉じハッシュは正本と別物という既存契約を維持する。
        candidate = _rstrip_ascii_whitespace(line)
        leading_spaces = len(candidate) - len(candidate.lstrip(" "))
        if 1 <= leading_spaces <= 3 and candidate[leading_spaces:] == heading:
            occurrences.append(index)
    return occurrences


def _closing_hash_heading_occurrences(lines, heading, ignored_states):
    """Indices of CommonMark closing-hash aliases of the managed heading."""
    occurrences = []
    for index, line in enumerate(lines):
        if ignored_states[index]:
            continue

        # 正本のraw headingは動的regexにせず、固定文字列として比較する。
        # その後ろだけを静的closing-sequence regexへ渡すことで、入力由来の
        # inline内容を解釈せず既知のblock-level aliasだけを判定する。
        leading_spaces = len(line) - len(line.lstrip(" "))
        if leading_spaces > 3:
            continue
        candidate = line[leading_spaces:]
        if not candidate.startswith(heading):
            continue
        suffix = candidate[len(heading) :]
        if _ATX_CLOSING_SEQUENCE_RE.fullmatch(suffix):
            occurrences.append(index)
    return occurrences


def _split_lines(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # drop the empty tail produced by a trailing newline
    return lines


def _strip_trailing_blank(lines):
    while lines and _is_ascii_blank(lines[-1]):
        lines.pop()


def validate_block(block_lines):
    """Return the block's heading line after checking the merge invariants."""
    if not block_lines or not _H2_RE.match(block_lines[0]):
        raise MergeError("block must start with an H2 heading line ('## ...')")
    heading = _rstrip_ascii_whitespace(block_lines[0])
    if _ATX_CLOSING_SEQUENCE_RE.search(heading):
        # block側でclosing-hashを正本にすると、素のH2とのidentityが曖昧な
        # まま固定される。書込み前にplain formへ直すことを要求する。
        raise MergeError(
            "block heading must use plain form without a closing hash sequence"
        )
    region_scan = _markdown_region_scan(block_lines)
    if region_scan[2]:
        # An unclosed fence in the block would swallow whatever follows the
        # merged section in the target — refuse instead of corrupting.
        raise MergeError("block ends inside an unclosed code fence")
    if region_scan[3]:
        raise MergeError(
            "block ends inside an unclosed raw HTML block type %d; close its "
            "explicit end condition before merging" % region_scan[3]
        )

    # block内setextを初回appendで受理すると、次回replaceだけが同じ構造を
    # 拒否してapply-twiceが収束しない。target spanと同じ保守的走査・固定診断を
    # 書込み前に適用し、append/replaceのvalidation境界を一致させる。
    _reject_setext_in_span(block_lines, 0, len(block_lines))

    boundaries = [
        index
        for index, line in enumerate(block_lines)
        if not region_scan[0][index] and _BOUNDARY_RE.match(line)
    ]
    if boundaries != [0]:
        raise MergeError(
            "block must contain exactly one H1/H2-level heading outside "
            "literal regions: its own first line"
        )
    return heading


def _reject_setext_in_span(lines, start, end):
    """Refuse to replace across a possible setext heading.

    A ``===`` or ``---`` run directly under a non-blank line is (or may be)
    a real heading that ``_BOUNDARY_RE`` cannot see. Replacing across it
    would delete a section boundary without any error, so stop and report.
    """
    # setext-looking lines inside frontmatter/fence/raw HTML are literal data,
    # not a Markdown boundary. Reuse the same combined mask as ATX scanning.
    states = _markdown_region_scan(lines)[0]
    for index in range(start + 1, end):
        if states[index] or not _SETEXT_RE.match(lines[index]):
            continue
        if index - 1 <= start or states[index - 1]:
            continue
        previous = lines[index - 1]
        if _is_ascii_blank(previous) or _BOUNDARY_RE.match(previous):
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

    target_regions = _markdown_region_scan(doc_lines)
    if target_regions[2]:
        # CommonMark runs an unclosed fence to EOF, so the section would
        # extend to EOF and a replace would rewrite the whole visually
        # swallowed tail. Malformed input: stop and report instead.
        raise MergeError(
            "target ends inside an unclosed code fence; close it before merging"
        )
    if target_regions[3]:
        # CommonMark permits explicit-end HTML blocks to reach EOF. Appending a
        # managed H2 there would hide it inside raw HTML and break idempotence,
        # so this mutating tool deliberately applies a stricter contract.
        raise MergeError(
            "target ends inside an unclosed raw HTML block type %d; close its "
            "explicit end condition before merging" % target_regions[3]
        )

    ignored_states = target_regions[0]
    occurrences = [
        index
        for index, line in enumerate(doc_lines)
        if (
            not ignored_states[index]
            and _rstrip_ascii_whitespace(line) == heading
        )
    ]
    indented_occurrences = _indented_heading_occurrences(
        doc_lines,
        heading,
        ignored_states,
    )
    if indented_occurrences:
        # list/containerを完全解析していないため、自動で列0へ移動すると文書構造を
        # 変え得る。入力由来の見出し本文や行番号も反射せず、固定文言で拒否する。
        raise MergeError(
            "target contains an indented managed H2 outside literal regions; "
            "move it to column 0, rename it, or deduplicate it before merging"
        )
    closing_hash_occurrences = _closing_hash_heading_occurrences(
        doc_lines,
        heading,
        ignored_states,
    )
    if closing_hash_occurrences:
        # CommonMark renderer上は素の正本H2と同じ本文になるため、別物として
        # append/replaceすると意味上の重複を作る。自動書換えはせず固定文言で拒否する。
        raise MergeError(
            "target contains a closing-hash managed H2 alias outside literal "
            "regions; convert it to the plain heading form, rename it, or "
            "deduplicate it before merging"
        )
    if len(occurrences) > 1:
        raise MergeError(
            "target contains %d copies of '%s' outside literal regions; "
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


def _inspect_target(target, allow_multiple_links=False):
    """Return TARGET's ``lstat`` result, refusing link ambiguity."""

    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        return None

    # Replacing a symlink atomically changes the link itself, while the old
    # direct write followed it. Refuse instead of silently changing which
    # filesystem object the command updates.
    if stat.S_ISLNK(target_stat.st_mode):
        raise MergeError("target is a symbolic link; refusing atomic replacement")

    # FIFOs, sockets, devices, and directories can block or perform side
    # effects when read. The command only rewrites ordinary regular files.
    if not stat.S_ISREG(target_stat.st_mode):
        raise MergeError("target is not a regular file; refusing atomic replacement")

    # An atomic rename necessarily breaks a hard-link set. Updating the shared
    # inode in place would preserve the links but reintroduce partial writes, so
    # require the caller to choose an ordinary single-link target explicitly.
    if target_stat.st_nlink > 1 and not allow_multiple_links:
        raise MergeError("target has multiple hard links; refusing atomic replacement")

    return target_stat


def _open_windows_atomic_temporary(target):
    """Create a Windows temp with an owner/SYSTEM-only protected DACL."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        )

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert_descriptor = (
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    )
    convert_descriptor.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    )
    convert_descriptor.restype = wintypes.BOOL

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = (wintypes.HLOCAL,)
    local_free.restype = wintypes.HLOCAL

    security_descriptor = wintypes.LPVOID()
    sddl_revision_1 = 1
    # P marks the DACL protected from parent inheritance. SYSTEM remains able
    # to service the file; OW grants full access only to the assigned owner.
    if not convert_descriptor(
        "D:P(A;;FA;;;SY)(A;;FA;;;OW)",
        sddl_revision_1,
        ctypes.byref(security_descriptor),
        None,
    ):
        _raise_windows_api_error(ctypes.get_last_error(), target)
    security_attributes = SecurityAttributes(
        ctypes.sizeof(SecurityAttributes),
        security_descriptor,
        False,
    )

    generic_write = 0x40000000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    create_new = 1
    file_attribute_normal = 0x0080
    invalid_handle = ctypes.c_void_p(-1).value

    try:
        for _attempt in range(32):
            temporary = target.parent / (
                ".%s.%s.tmp" % (target.name, secrets.token_hex(12))
            )
            handle = create_file(
                os.path.abspath(os.fspath(temporary)),
                generic_write,
                share_read_write_delete,
                ctypes.byref(security_attributes),
                create_new,
                file_attribute_normal,
                None,
            )
            if handle == invalid_handle:
                error_code = ctypes.get_last_error()
                if error_code in (80, 183):
                    continue
                _raise_windows_api_error(error_code, temporary)

            try:
                descriptor = msvcrt.open_osfhandle(
                    handle,
                    os.O_WRONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOINHERIT", 0),
                )
            except BaseException as descriptor_error:
                close_handle(handle)
                # No Python fd exists from which to retain an identity
                # fingerprint. Leave the unpredictable private file behind
                # rather than risk unlinking a path another writer swapped,
                # and surface its exact name through the structured CLI path.
                raise AtomicCommitError(
                    "Windows temporary descriptor setup failed",
                    committed=False,
                    recovered=True,
                    artifacts=(temporary,),
                ) from descriptor_error
            return descriptor, temporary
    finally:
        local_free(security_descriptor)
    raise OSError("could not allocate an exclusive temporary file")


def _open_atomic_temporary(target):
    """Create an owner-only exclusive same-directory temp for TARGET."""

    if os.name == "nt":
        return _open_windows_atomic_temporary(target)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)

    # Same-directory creation keeps the final os.replace on one filesystem.
    # O_EXCL prevents a pre-created link from redirecting the temporary write.
    for _attempt in range(32):
        temporary = target.parent / (
            ".%s.%s.tmp" % (target.name, secrets.token_hex(12))
        )
        try:
            # Content is written only after creation, so start at 0600.
            # Existing-target mode is copied before commit on POSIX.
            descriptor = os.open(temporary, flags, 0o600)
        except FileExistsError:
            continue
        try:
            # open(2) applies umask even to an explicit 0600 request. Restore
            # the exact private mode before any document bytes are written.
            os.fchmod(descriptor, 0o600)
        except BaseException as mode_error:
            try:
                os.close(descriptor)
            except BaseException:
                pass
            raise AtomicCommitError(
                "POSIX temporary private-mode setup failed",
                committed=False,
                recovered=True,
                artifacts=_existing_artifacts((temporary,)),
            ) from mode_error
        return descriptor, temporary
    raise OSError("could not allocate an exclusive temporary file")


def _copy_posix_metadata(target, descriptor, target_stat):
    """Copy bounded metadata that an inode-replacing commit would lose."""

    if hasattr(os, "fchown"):
        os.fchown(descriptor, target_stat.st_uid, target_stat.st_gid)
    os.fchmod(descriptor, stat.S_IMODE(target_stat.st_mode))

    # Linux and several Unix variants expose ACL/security labels through
    # extended attributes. Preserve a bounded set or abort before commit;
    # silently dropping metadata is less safe than leaving the target intact.
    if not all(
        hasattr(os, name)
        for name in ("listxattr", "getxattr", "setxattr")
    ):
        return
    names = os.listxattr(target, follow_symlinks=False)
    if len(names) > _MAX_XATTR_COUNT:
        raise MergeError("target has too many extended attributes to preserve")

    total_bytes = 0
    for name in names:
        value = os.getxattr(target, name, follow_symlinks=False)
        total_bytes += len(os.fsencode(name)) + len(value)
        if total_bytes > _MAX_XATTR_BYTES:
            raise MergeError("target extended attributes exceed the preservation limit")
        os.setxattr(descriptor, name, value)


def _stat_fingerprint(value):
    """Return fields that expose replacement or mutation of one target."""

    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        getattr(value, "st_file_attributes", None),
        getattr(value, "st_reparse_tag", None),
    )


def _open_windows_regular_read_descriptor(
    target,
    missing_ok,
    reject_encrypted=True,
):
    """Open TARGET without traversing a Windows reparse point."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = (wintypes.HANDLE,)
    get_file_type.restype = wintypes.DWORD
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    invalid_handle = ctypes.c_void_p(-1).value

    handle = create_file(
        os.path.abspath(os.fspath(target)),
        generic_read,
        share_read_write_delete,
        None,
        open_existing,
        file_flag_open_reparse_point | file_flag_backup_semantics,
        None,
    )
    if handle == invalid_handle:
        error_code = ctypes.get_last_error()
        if missing_ok and error_code in (2, 3):
            return None
        _raise_windows_api_error(error_code, target)

    descriptor = None
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            _raise_windows_api_error(ctypes.get_last_error(), target)

        file_type_disk = 0x0001
        file_attribute_directory = 0x0010
        file_attribute_reparse_point = 0x0400
        file_attribute_encrypted = 0x4000
        if (
            get_file_type(handle) != file_type_disk
            or information.dwFileAttributes & file_attribute_directory
        ):
            raise MergeError(
                "target is not a regular file; refusing atomic replacement"
            )
        if information.dwFileAttributes & file_attribute_reparse_point:
            raise MergeError(
                "target is a symbolic link or reparse point; "
                "refusing atomic replacement"
            )
        if (
            reject_encrypted
            and information.dwFileAttributes & file_attribute_encrypted
        ):
            raise MergeError(
                "target uses EFS encryption; refusing plaintext temporary write"
            )
        if information.nNumberOfLinks > 1:
            raise MergeError(
                "target has multiple hard links; refusing atomic replacement"
            )

        # open_osfhandle transfers ownership of HANDLE to the Python fd.
        # From this point os.close/fdopen is the only valid closer.
        descriptor = msvcrt.open_osfhandle(
            handle,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        handle = None
        return descriptor
    finally:
        if handle is not None:
            close_handle(handle)


def _open_regular_read_descriptor(
    target,
    missing_ok=False,
    reject_encrypted=True,
):
    """Open TARGET for a bounded-type read without following a link."""

    if os.name == "nt":
        return _open_windows_regular_read_descriptor(
            target,
            missing_ok,
            reject_encrypted=reject_encrypted,
        )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    # A FIFO opened read-only can otherwise wait forever before fstat gets a
    # chance to reject it. O_NONBLOCK has no effect on regular-file reads.
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        return os.open(target, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):
            raise MergeError(
                "target is a symbolic link; refusing atomic replacement"
            ) from error
        raise


def _assert_windows_descriptor_owned_by_effective_owner(descriptor, target):
    """Refuse replacing a Windows file owned by a different effective SID."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class TokenOwner(ctypes.Structure):
        _fields_ = (("Owner", wintypes.LPVOID),)

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi32.GetSecurityInfo
    get_security.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    )
    get_security.restype = wintypes.DWORD
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    open_process_token.restype = wintypes.BOOL
    open_thread_token = advapi32.OpenThreadToken
    open_thread_token.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.BOOL,
        ctypes.POINTER(wintypes.HANDLE),
    )
    open_thread_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_token_information.restype = wintypes.BOOL
    equal_sid = advapi32.EqualSid
    equal_sid.argtypes = (wintypes.LPVOID, wintypes.LPVOID)
    equal_sid.restype = wintypes.BOOL

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    get_current_thread = kernel32.GetCurrentThread
    get_current_thread.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = (wintypes.HLOCAL,)
    local_free.restype = wintypes.HLOCAL

    owner_sid = wintypes.LPVOID()
    security_descriptor = wintypes.LPVOID()
    owner_security_information = 0x00000001
    se_file_object = 1
    error_code = get_security(
        msvcrt.get_osfhandle(descriptor),
        se_file_object,
        owner_security_information,
        ctypes.byref(owner_sid),
        None,
        None,
        None,
        ctypes.byref(security_descriptor),
    )
    if error_code:
        _raise_windows_api_error(error_code, target)

    token = wintypes.HANDLE()
    token_query = 0x0008
    try:
        # An impersonating thread creates objects with its effective token.
        # Fall back to the process token only when no thread token exists.
        if not open_thread_token(
            get_current_thread(),
            token_query,
            True,
            ctypes.byref(token),
        ):
            error_no_token = 1008
            if ctypes.get_last_error() != error_no_token or not open_process_token(
                get_current_process(),
                token_query,
                ctypes.byref(token),
            ):
                _raise_windows_api_error(ctypes.get_last_error(), target)

        token_owner = 4
        required = wintypes.DWORD()
        get_token_information(
            token,
            token_owner,
            None,
            0,
            ctypes.byref(required),
        )
        if required.value == 0:
            _raise_windows_api_error(ctypes.get_last_error(), target)
        token_buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            token_owner,
            token_buffer,
            required.value,
            ctypes.byref(required),
        ):
            _raise_windows_api_error(ctypes.get_last_error(), target)
        default_owner_sid = ctypes.cast(
            token_buffer,
            ctypes.POINTER(TokenOwner),
        ).contents.Owner
        if not equal_sid(owner_sid, default_owner_sid):
            raise MergeError(
                "target owner differs from the effective Windows token owner; "
                "refusing replacement"
            )
    finally:
        if token:
            close_handle(token)
        local_free(security_descriptor)


def _assert_windows_owner_only_dacl(descriptor, target):
    """Require the private Windows temporary's exact protected DACL."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class AclSizeInformation(ctypes.Structure):
        _fields_ = (
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        )

    class AceHeader(ctypes.Structure):
        _fields_ = (
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        )

    class AccessAllowedAce(ctypes.Structure):
        _fields_ = (
            ("Header", AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        )

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi32.GetSecurityInfo
    get_security.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    )
    get_security.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_control.restype = wintypes.BOOL
    get_acl_information = advapi32.GetAclInformation
    get_acl_information.argtypes = (
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_acl_information.restype = wintypes.BOOL
    get_ace = advapi32.GetAce
    get_ace.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    )
    get_ace.restype = wintypes.BOOL
    convert_sid = advapi32.ConvertStringSidToSidW
    convert_sid.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    )
    convert_sid.restype = wintypes.BOOL
    equal_sid = advapi32.EqualSid
    equal_sid.argtypes = (wintypes.LPVOID, wintypes.LPVOID)
    equal_sid.restype = wintypes.BOOL

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = (wintypes.HLOCAL,)
    local_free.restype = wintypes.HLOCAL

    dacl = wintypes.LPVOID()
    security_descriptor = wintypes.LPVOID()
    dacl_security_information = 0x00000004
    se_file_object = 1
    error_code = get_security(
        msvcrt.get_osfhandle(descriptor),
        se_file_object,
        dacl_security_information,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if error_code:
        _raise_windows_api_error(error_code, target)

    expected_sids = []
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not get_control(
            security_descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            _raise_windows_api_error(ctypes.get_last_error(), target)

        # The creation SDDL is a security boundary: inheritance must remain
        # disabled and no third ACE may gain access before the rename.
        se_dacl_protected = 0x1000
        if not dacl or not control.value & se_dacl_protected:
            raise MergeError(
                "replacement temporary DACL changed before commit"
            )

        information = AclSizeInformation()
        acl_size_information = 2
        if not get_acl_information(
            dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            acl_size_information,
        ):
            _raise_windows_api_error(ctypes.get_last_error(), target)
        if information.AceCount != 2:
            raise MergeError(
                "replacement temporary DACL changed before commit"
            )

        # Match semantic ACEs instead of serialized descriptor bytes: Windows
        # may canonicalize storage while retaining the same access policy.
        for sid_text in ("S-1-5-18", "S-1-3-4"):
            sid = wintypes.LPVOID()
            if not convert_sid(sid_text, ctypes.byref(sid)):
                _raise_windows_api_error(ctypes.get_last_error(), target)
            expected_sids.append(sid)

        matched = [False, False]
        file_all_access = 0x001F01FF
        access_allowed_ace_type = 0
        for index in range(information.AceCount):
            ace_pointer = wintypes.LPVOID()
            if not get_ace(dacl, index, ctypes.byref(ace_pointer)):
                _raise_windows_api_error(ctypes.get_last_error(), target)
            ace = ctypes.cast(
                ace_pointer,
                ctypes.POINTER(AccessAllowedAce),
            ).contents
            if (
                ace.Header.AceType != access_allowed_ace_type
                or ace.Header.AceFlags != 0
                or ace.Mask != file_all_access
            ):
                raise MergeError(
                    "replacement temporary DACL changed before commit"
                )
            sid_pointer = ctypes.c_void_p(
                ace_pointer.value + AccessAllowedAce.SidStart.offset
            )
            sid_matches = [
                bool(equal_sid(sid_pointer, expected))
                for expected in expected_sids
            ]
            if sum(sid_matches) != 1:
                raise MergeError(
                    "replacement temporary DACL changed before commit"
                )
            matched[sid_matches.index(True)] = True
        if matched != [True, True]:
            raise MergeError(
                "replacement temporary DACL changed before commit"
            )
    finally:
        for sid in expected_sids:
            local_free(sid)
        local_free(security_descriptor)


def _read_regular_file_snapshot(
    target,
    missing_ok=False,
    allow_multiple_links=False,
    require_effective_owner=True,
    reject_encrypted=True,
):
    """Read one stable regular-file object and verify its path identity."""

    descriptor = _open_regular_read_descriptor(
        target,
        missing_ok=missing_ok,
        reject_encrypted=reject_encrypted,
    )
    if descriptor is None:
        return None, b""

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MergeError(
                "target is not a regular file; refusing atomic replacement"
            )
        if before.st_nlink > 1 and not allow_multiple_links:
            raise MergeError(
                "target has multiple hard links; refusing atomic replacement"
            )
        if os.name == "nt" and require_effective_owner:
            _assert_windows_descriptor_owned_by_effective_owner(
                descriptor,
                target,
            )

        # OPEN_REPARSE_POINT/O_NOFOLLOW protects the open itself. The path
        # checks also detect a rename/swap after the handle was acquired.
        path_before = _inspect_target(
            target,
            allow_multiple_links=allow_multiple_links,
        )
        if (
            path_before is None
            or not _same_file_identity(path_before, before)
        ):
            raise MergeError("target changed while being opened")

        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read()
            after = os.fstat(stream.fileno())
            path_after = _inspect_target(
                target,
                allow_multiple_links=allow_multiple_links,
            )
            if _stat_fingerprint(after) != _stat_fingerprint(before):
                raise MergeError("target changed while being read")
            if (
                path_after is None
                or not _same_file_identity(path_after, after)
            ):
                raise MergeError("target path changed while being read")
        return after, raw
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_target_unchanged(target, target_stat, original_bytes):
    """Fail before commit when another writer changed TARGET."""

    current_before, current_bytes = _read_regular_file_snapshot(
        target,
        missing_ok=True,
    )
    if target_stat is None:
        if current_before is not None:
            raise MergeError("target appeared during merge; refusing to overwrite it")
        return
    if current_before is None:
        raise MergeError("target disappeared during merge")
    if _stat_fingerprint(current_before) != _stat_fingerprint(target_stat):
        raise MergeError("target metadata changed during merge")

    if current_bytes != original_bytes:
        raise MergeError("target content changed during merge")


def _move_file_windows_no_replace_raw(source, destination):
    """Return zero or the Win32 error from a no-replace durable move."""

    import ctypes
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    move_file.restype = wintypes.BOOL
    movefile_write_through = 0x00000008
    if move_file(
        os.path.abspath(os.fspath(source)),
        os.path.abspath(os.fspath(destination)),
        movefile_write_through,
    ):
        return 0
    return ctypes.get_last_error()


def _verified_replacement_snapshot(
    temporary,
    expected_stat,
    expected_bytes,
):
    """Verify that the commit path still names the file this call wrote."""

    current_stat, current_bytes = _read_regular_file_snapshot(temporary)
    if (
        current_stat is None
        or _stat_fingerprint(current_stat) != _stat_fingerprint(expected_stat)
        or current_bytes != expected_bytes
    ):
        raise MergeError("replacement temporary changed before commit")
    if os.name == "nt":
        descriptor = _open_windows_regular_read_descriptor(
            temporary,
            missing_ok=False,
        )
        try:
            _assert_windows_owner_only_dacl(descriptor, temporary)
        finally:
            os.close(descriptor)
    return current_stat


def _move_new_windows_file(
    target,
    temporary,
    expected_stat,
    expected_bytes,
):
    """Atomically install a missing Windows target without replacement."""

    try:
        current_stat = _verified_replacement_snapshot(
            temporary,
            expected_stat,
            expected_bytes,
        )
    except BaseException as verification_error:
        raise AtomicCommitError(
            "Windows no-replace temporary could not be verified",
            committed=False,
            recovered=True,
            artifacts=_existing_artifacts((temporary,)),
        ) from verification_error

    native_failure = None
    try:
        error_code = _move_file_windows_no_replace_raw(temporary, target)
    except BaseException as move_error:
        # KeyboardInterrupt can arrive after MoveFileExW changed the name but
        # before Python observed its return value. Reconcile both names before
        # deciding whether cleanup is safe.
        native_failure = move_error
        error_code = None

    if native_failure is not None:
        if _path_matches_expected(target, current_stat, expected_bytes):
            raise AtomicCommitError(
                "Windows no-replace move raised after the target was "
                "observably committed",
                committed=True,
                recovered=False,
                artifacts=_existing_artifacts((temporary,)),
            ) from native_failure

        try:
            target_missing = _inspect_target(target) is None
        except (MergeError, OSError):
            target_missing = False
        if target_missing and _path_matches_expected(
            temporary,
            current_stat,
            expected_bytes,
        ):
            try:
                _unlink_owned_path(temporary, current_stat)
            except BaseException:
                raise AtomicCommitError(
                    "Windows no-replace move was interrupted before commit "
                    "and temporary cleanup was unsafe",
                    committed=False,
                    recovered=True,
                    artifacts=_existing_artifacts((temporary,)),
                ) from native_failure
            raise native_failure

        raise AtomicCommitError(
            "Windows no-replace move was interrupted and its state is "
            "ambiguous",
            committed=None,
            recovered=False,
            artifacts=_existing_artifacts((target, temporary)),
        ) from native_failure

    if error_code:
        if _path_matches_expected(target, current_stat, expected_bytes):
            raise AtomicCommitError(
                "Windows no-replace move reported failure after the target "
                "was observably committed",
                committed=True,
                recovered=False,
                artifacts=_existing_artifacts((temporary,)),
            )
        try:
            _unlink_owned_path(temporary, current_stat)
        except BaseException:
            raise AtomicCommitError(
                "Windows no-replace move failed and temporary cleanup "
                "was unsafe",
                committed=False,
                recovered=True,
                artifacts=_existing_artifacts((temporary,)),
            )
        _raise_windows_api_error(error_code, target)

    if not _path_matches_expected(target, current_stat, expected_bytes):
        raise AtomicCommitError(
            "Windows no-replace move succeeded but the target could not "
            "be verified",
            committed=True,
            recovered=False,
            artifacts=_existing_artifacts((temporary,)),
        )


def _replace_file_windows_raw(target, temporary, backup):
    """Return zero or the Win32 error from ``ReplaceFileW``."""

    import ctypes
    from ctypes import wintypes

    replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    replace_file.restype = wintypes.BOOL
    if replace_file(
        os.path.abspath(os.fspath(target)),
        os.path.abspath(os.fspath(temporary)),
        os.path.abspath(os.fspath(backup)),
        0,
        None,
        None,
    ):
        return 0
    return ctypes.get_last_error()


def _open_windows_backup_placeholder(target):
    """Reserve one unpredictable same-directory ReplaceFileW backup name."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    for _attempt in range(32):
        backup = target.parent / (
            ".%s.%s.replace-backup" % (target.name, secrets.token_hex(12))
        )
        try:
            descriptor = os.open(backup, flags, 0o600)
        except FileExistsError:
            continue
        except BaseException as open_error:
            raise _windows_commit_state_error(
                "Windows recovery backup could not be opened safely",
                committed=False,
                recovered=True,
                paths=(backup,),
            ) from open_error

        placeholder_stat = None
        try:
            placeholder_stat = os.fstat(descriptor)
            os.fsync(descriptor)
        except BaseException as setup_error:
            # 予約名を作った後の失敗では、閉じられたことと同じ file
            # identity のままであることを確認できた場合だけ片付ける。
            try:
                os.close(descriptor)
            except BaseException:
                raise _windows_commit_state_error(
                    "Windows recovery backup setup failed while closing its placeholder",
                    committed=False,
                    recovered=True,
                    paths=(backup,),
                ) from setup_error
            if placeholder_stat is None:
                raise _windows_commit_state_error(
                    "Windows recovery backup setup failed before its identity "
                    "could be recorded",
                    committed=False,
                    recovered=True,
                    paths=(backup,),
                ) from setup_error
            try:
                _unlink_owned_path(backup, placeholder_stat)
            except BaseException:
                raise _windows_commit_state_error(
                    "Windows recovery backup setup failed and its placeholder cleanup was unsafe",
                    committed=False,
                    recovered=True,
                    paths=(backup,),
                ) from setup_error
            raise AtomicCommitError(
                "Windows recovery backup setup failed before replacement",
                committed=False,
                recovered=True,
                artifacts=(),
            ) from setup_error
        try:
            os.close(descriptor)
        except BaseException as close_error:
            # close 失敗時は descriptor が閉じたと推測せず、回復用の
            # placeholder を残して利用者へ明示する。
            raise _windows_commit_state_error(
                "Windows recovery backup placeholder could not be closed safely",
                committed=False,
                recovered=True,
                paths=(backup,),
            ) from close_error
        return backup, placeholder_stat
    raise OSError("could not allocate an exclusive recovery backup")


def _same_file_identity(left, right):
    """Compare stable filesystem identity without following path aliases."""

    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _path_matches_expected(
    path,
    expected_stat,
    expected_bytes,
    allow_multiple_links=False,
):
    """Return whether PATH is the expected file object and exact bytes."""

    try:
        current, current_bytes = _read_regular_file_snapshot(
            path,
            missing_ok=True,
            allow_multiple_links=allow_multiple_links,
        )
        return (
            current is not None
            and _same_file_identity(current, expected_stat)
            and current_bytes == expected_bytes
        )
    except (MergeError, OSError):
        return False


def _unlink_owned_path(path, expected_stat):
    """Delete PATH only while it still names the object this call created."""

    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if not _same_file_identity(current, expected_stat):
        raise MergeError("recovery artifact identity changed; refusing cleanup")
    path.unlink()


def _existing_artifacts(paths):
    """Return paths that exist or whose absence cannot be proved safely."""

    existing = []
    for path in paths:
        if path is None:
            continue
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except BaseException:
            # Permission/I/O failure after a partial commit must not mask the
            # structured state. Interrupts are also conservatively retained:
            # this formatter must never replace the primary commit outcome.
            pass
        existing.append(path)
    return existing


def _owned_artifacts(path_identities):
    """Return only still-owned paths, retaining uninspectable candidates."""

    owned = []
    for path, expected_stat in path_identities:
        if path is None:
            continue
        try:
            current = path.lstat()
        except FileNotFoundError:
            continue
        except BaseException:
            # If identity cannot be inspected, keep the candidate visible for
            # manual recovery. A proven mismatch, by contrast, is foreign.
            owned.append(path)
            continue
        if expected_stat is None or _same_file_identity(
            current,
            expected_stat,
        ):
            owned.append(path)
    return owned


def _windows_commit_state_error(message, committed, recovered, paths):
    """Create a structured partial-commit error and retain every artifact."""

    artifacts = _existing_artifacts(paths)
    suffix = ""
    if artifacts:
        suffix = "; retained artifacts: " + ", ".join(
            ascii(os.fspath(path)) for path in artifacts
        )
    return AtomicCommitError(
        message + suffix,
        committed=committed,
        recovered=recovered,
        artifacts=artifacts,
    )


def _raise_windows_api_error(error_code, target):
    """Raise one native-style error after a safe rollback/cleanup."""

    import ctypes

    message = (
        ctypes.FormatError(error_code)
        if hasattr(ctypes, "FormatError")
        else "Windows error %d" % error_code
    )
    raise OSError(
        error_code,
        message,
        os.path.abspath(os.fspath(target)),
    )


def _commit_existing_windows(
    target,
    temporary,
    target_stat,
    original_bytes,
    expected_replacement_stat=None,
    expected_replacement_bytes=None,
):
    """Own TEMPORARY and recover every documented ReplaceFileW partial state."""

    try:
        if (
            expected_replacement_stat is not None
            and expected_replacement_bytes is not None
        ):
            temporary_stat = _verified_replacement_snapshot(
                temporary,
                expected_replacement_stat,
                expected_replacement_bytes,
            )
            replacement_bytes = expected_replacement_bytes
        else:
            # State-machine unit fixtures call this helper directly on all
            # platforms. Production always supplies the creation fingerprint.
            temporary_stat, replacement_bytes = _read_regular_file_snapshot(
                temporary,
            )
            if temporary_stat is None:
                raise MergeError(
                    "Windows replacement temporary disappeared"
                )
    except BaseException as temporary_error:
        raise _windows_commit_state_error(
            "Windows replacement temporary could not be verified before commit",
            committed=False,
            recovered=True,
            paths=(temporary,),
        ) from temporary_error

    backup = None
    placeholder_stat = None
    try:
        backup, placeholder_stat = _open_windows_backup_placeholder(target)
    except BaseException as setup_error:
        try:
            _unlink_owned_path(temporary, temporary_stat)
        except BaseException:
            setup_artifacts = (
                setup_error.artifacts
                if isinstance(setup_error, AtomicCommitError)
                else ()
            )
            raise _windows_commit_state_error(
                "Windows commit setup failed and temporary cleanup was unsafe",
                committed=False,
                recovered=True,
                paths=tuple(setup_artifacts) + (temporary,),
            ) from setup_error
        raise

    native_failure = None
    try:
        error_code = _replace_file_windows_raw(target, temporary, backup)
    except BaseException as error:
        # A mock or loader failure is reconciled exactly like an unknown
        # Win32 result. This also reconciles an interrupt delivered just after
        # the native call returned from a partial name transition.
        native_failure = error
        error_code = None

    if error_code == 0:
        # Success should move the replacement object to TARGET and the old
        # target object to BACKUP. Verify both transitions before deleting the
        # only recovery copy.
        if not _path_matches_expected(
            target,
            temporary_stat,
            replacement_bytes,
        ):
            raise _windows_commit_state_error(
                "Windows replacement reported success but the committed "
                "target could not be verified",
                committed=True,
                recovered=False,
                paths=(backup, temporary),
            )
        if not _path_matches_expected(backup, target_stat, original_bytes):
            raise _windows_commit_state_error(
                "Windows replacement committed but its recovery backup "
                "could not be verified",
                committed=True,
                recovered=False,
                paths=(backup,),
            )
        try:
            _unlink_owned_path(backup, target_stat)
        except BaseException:
            raise _windows_commit_state_error(
                "Windows replacement committed but its recovery backup "
                "could not be removed",
                committed=True,
                recovered=False,
                paths=(backup,),
            )
        return

    original_at_target = _path_matches_expected(
        target,
        target_stat,
        original_bytes,
    )
    original_at_backup = _path_matches_expected(
        backup,
        target_stat,
        original_bytes,
    )
    replacement_at_target = _path_matches_expected(
        target,
        temporary_stat,
        replacement_bytes,
    )

    if replacement_at_target:
        # The raw API reported failure, but this invocation's exact temporary
        # object and bytes are now installed. Preserve every recovery artifact
        # and report the observed commit instead of calling it a rollback.
        raise _windows_commit_state_error(
            "Windows replacement reported failure after the replacement "
            "was observably committed",
            committed=True,
            recovered=False,
            paths=(backup, temporary),
        )

    restore_interruption = None
    if original_at_backup:
        # ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 (1177) documents this state.
        # Restore with no replace; a concurrently created target wins and both
        # owned recovery files are retained for explicit manual resolution.
        try:
            target_missing = _inspect_target(target) is None
        except BaseException as inspect_error:
            raise _windows_commit_state_error(
                "Windows replacement failed with the original in backup, "
                "but the target path could not be inspected safely",
                committed=None,
                recovered=False,
                paths=(target, backup, temporary),
            ) from inspect_error
        if target_missing:
            try:
                move_error = _move_file_windows_no_replace_raw(backup, target)
            except BaseException as restore_error:
                if _path_matches_expected(
                    target,
                    target_stat,
                    original_bytes,
                ):
                    original_at_target = True
                    restore_interruption = restore_error
                    move_error = None
                else:
                    try:
                        target_missing = _inspect_target(target) is None
                    except (MergeError, OSError):
                        target_missing = False
                    if target_missing and _path_matches_expected(
                        backup,
                        target_stat,
                        original_bytes,
                    ):
                        raise _windows_commit_state_error(
                            "Windows replacement rollback was interrupted "
                            "before the original backup was restored",
                            committed=False,
                            recovered=False,
                            paths=(backup, temporary),
                        ) from restore_error
                    raise _windows_commit_state_error(
                        "Windows replacement rollback was interrupted and "
                        "its state is ambiguous",
                        committed=None,
                        recovered=False,
                        paths=(target, backup, temporary),
                    ) from restore_error
            if move_error:
                raise _windows_commit_state_error(
                    "Windows replacement failed and the original backup "
                    "could not be restored without replacement",
                    committed=False,
                    recovered=False,
                    paths=(backup, temporary),
                )
            original_at_target = _path_matches_expected(
                target,
                target_stat,
                original_bytes,
            )
            if not original_at_target:
                raise _windows_commit_state_error(
                    "Windows replacement rollback completed but the restored "
                    "target could not be verified",
                    committed=False,
                    recovered=False,
                    paths=(target, backup, temporary),
                )
        else:
            raise _windows_commit_state_error(
                "Windows replacement failed after another target appeared; "
                "original backup was retained",
                committed=False,
                recovered=False,
                paths=(backup, temporary),
            )

    if original_at_target:
        # 1175/1176 and ordinary failures retain the original names when a
        # backup was supplied. Delete only objects whose identity is ours.
        try:
            _unlink_owned_path(temporary, temporary_stat)
            if backup is not None:
                _unlink_owned_path(backup, placeholder_stat)
        except BaseException:
            raise _windows_commit_state_error(
                "Windows replacement failed; original was recovered but "
                "artifact cleanup was unsafe",
                committed=False,
                recovered=True,
                paths=(backup, temporary),
            )
        if native_failure is not None:
            raise native_failure
        if restore_interruption is not None:
            raise AtomicCommitError(
                "Windows replacement rollback was interrupted after the "
                "original target was observably restored",
                committed=False,
                recovered=True,
                artifacts=(),
            ) from restore_interruption
        _raise_windows_api_error(error_code, target)

    raise _windows_commit_state_error(
        "Windows replacement failed and the original target state is ambiguous",
        committed=None,
        recovered=False,
        paths=(backup, temporary),
    )


def _commit_new_posix(
    target,
    temporary,
    expected_stat,
    expected_bytes,
):
    """Install a missing POSIX target and own post-link cleanup state."""

    try:
        temporary_stat = _verified_replacement_snapshot(
            temporary,
            expected_stat,
            expected_bytes,
        )
    except BaseException as verification_error:
        raise AtomicCommitError(
            "POSIX no-replace temporary could not be verified",
            committed=False,
            recovered=True,
            artifacts=_existing_artifacts((temporary,)),
        ) from verification_error

    link_interruption = None
    try:
        # link(2) is an atomic no-replace commit on one filesystem.
        os.link(temporary, target, follow_symlinks=False)
    except BaseException as link_error:
        # Python can deliver KeyboardInterrupt just after link(2) succeeded.
        # Reconcile by identity before deciding this was a pre-commit failure.
        if _path_matches_expected(
            target,
            temporary_stat,
            expected_bytes,
            allow_multiple_links=True,
        ):
            link_interruption = link_error
        else:
            try:
                _unlink_owned_path(temporary, temporary_stat)
            except BaseException:
                raise AtomicCommitError(
                    "POSIX no-replace commit failed and temporary cleanup "
                    "was unsafe",
                    committed=False,
                    recovered=True,
                    artifacts=_existing_artifacts((temporary,)),
                ) from link_error
            raise

    if not _path_matches_expected(
        target,
        temporary_stat,
        expected_bytes,
        allow_multiple_links=True,
    ):
        raise AtomicCommitError(
            "POSIX target was linked but could not be verified",
            committed=True,
            recovered=False,
            artifacts=_existing_artifacts((temporary,)),
        )

    try:
        # Removing the private name leaves the committed target as the only
        # ordinary link. A persistent failure is a committed partial state,
        # not an ordinary pre-commit error.
        _unlink_owned_path(temporary, temporary_stat)
    except BaseException as cleanup_error:
        raise AtomicCommitError(
            "POSIX target was committed but its temporary hard link could "
            "not be removed",
            committed=True,
            recovered=False,
            artifacts=_existing_artifacts((temporary,)),
        ) from cleanup_error

    if not _path_matches_expected(target, temporary_stat, expected_bytes):
        raise AtomicCommitError(
            "POSIX target was committed but its final identity could not "
            "be verified",
            committed=True,
            recovered=False,
            artifacts=(),
        )
    if link_interruption is not None:
        raise AtomicCommitError(
            "POSIX no-replace link raised after the target was observably "
            "committed",
            committed=True,
            recovered=False,
            artifacts=(),
        ) from link_interruption


def _commit_existing_posix(
    target,
    temporary,
    target_stat,
    original_bytes,
    expected_stat,
    expected_bytes,
):
    """Replace one POSIX target and reconcile exceptions around rename."""

    try:
        current_stat = _verified_replacement_snapshot(
            temporary,
            expected_stat,
            expected_bytes,
        )
    except BaseException as verification_error:
        raise AtomicCommitError(
            "POSIX replacement temporary could not be verified",
            committed=False,
            recovered=True,
            artifacts=_existing_artifacts((temporary,)),
        ) from verification_error

    try:
        os.replace(temporary, target)
    except BaseException as replace_error:
        if _path_matches_expected(target, current_stat, expected_bytes):
            raise AtomicCommitError(
                "POSIX replace raised after the replacement was observably "
                "committed",
                committed=True,
                recovered=False,
                artifacts=_existing_artifacts((temporary,)),
            ) from replace_error
        if _path_matches_expected(target, target_stat, original_bytes):
            try:
                _unlink_owned_path(temporary, current_stat)
            except BaseException:
                raise AtomicCommitError(
                    "POSIX replace failed; original was recovered but "
                    "temporary cleanup was unsafe",
                    committed=False,
                    recovered=True,
                    artifacts=_existing_artifacts((temporary,)),
                ) from replace_error
            raise
        raise AtomicCommitError(
            "POSIX replace failed and the target state is ambiguous",
            committed=None,
            recovered=False,
            artifacts=_existing_artifacts((temporary,)),
        ) from replace_error

    if not _path_matches_expected(target, current_stat, expected_bytes):
        raise AtomicCommitError(
            "POSIX replace succeeded but the committed target could not "
            "be verified",
            committed=True,
            recovered=False,
            artifacts=_existing_artifacts((temporary,)),
        )


def _commit_temporary(
    target,
    temporary,
    target_stat,
    original_bytes,
    expected_replacement_stat,
    expected_replacement_bytes,
):
    """Install TEMPORARY while preserving platform security metadata."""

    if target_stat is None:
        if os.name == "nt":
            _move_new_windows_file(
                target,
                temporary,
                expected_replacement_stat,
                expected_replacement_bytes,
            )
        else:
            _commit_new_posix(
                target,
                temporary,
                expected_replacement_stat,
                expected_replacement_bytes,
            )
        return
    if os.name != "nt":
        _commit_existing_posix(
            target,
            temporary,
            target_stat,
            original_bytes,
            expected_replacement_stat,
            expected_replacement_bytes,
        )
        return

    # ReplaceFileW carries forward the documented DACL, attributes, and named
    # streams. A private backup makes 1176/1177 partial failures recoverable.
    _commit_existing_windows(
        target,
        temporary,
        target_stat,
        original_bytes,
        expected_replacement_stat,
        expected_replacement_bytes,
    )


def _atomic_write(target, data, target_stat, original_bytes):
    """Write DATA completely, then atomically replace TARGET."""

    descriptor = None
    temporary = None
    temporary_identity = None
    written_stat = None
    operation_error = None
    operation_traceback = None
    try:
        descriptor, temporary = _open_atomic_temporary(target)
        try:
            temporary_identity = os.fstat(descriptor)
        except BaseException as identity_error:
            # The exclusive name already exists, but without a handle-derived
            # identity a path unlink would be unsafe. Retain and report it.
            raise AtomicCommitError(
                "atomic temporary identity could not be recorded",
                committed=False,
                recovered=True,
                artifacts=_existing_artifacts((temporary,)),
            ) from identity_error
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        with stream:
            stream.write(data)
            stream.flush()
            if target_stat is not None and os.name != "nt":
                _copy_posix_metadata(target, stream.fileno(), target_stat)
            os.fsync(stream.fileno())
            written_stat = os.fstat(stream.fileno())

        # Recheck both identity/version metadata and bytes after the temporary
        # file is durable. Existing-path replace APIs are not compare-and-swap:
        # this detects changes visible before the check, while callers needing
        # a strict lost-update guarantee must serialize every writer.
        _assert_target_unchanged(target, target_stat, original_bytes)
        # Every commit primitive can cross an observable name-transition
        # boundary. Transfer ownership before entering the platform helper so
        # outer cleanup can never erase a recovery artifact after an exception.
        commit_temporary = temporary
        temporary = None
        _commit_temporary(
            target,
            commit_temporary,
            target_stat,
            original_bytes,
            written_stat,
            data,
        )
    except BaseException as error:
        # Delay propagation until every still-owned precommit resource has had
        # one bounded cleanup attempt. Cleanup failures must not replace the
        # primary operation error with an unstructured exception.
        operation_error = error
        operation_traceback = error.__traceback__

    cleanup_errors = []
    if descriptor is not None:
        try:
            os.close(descriptor)
        except BaseException as close_error:
            cleanup_errors.append(close_error)
    if temporary is not None and temporary_identity is not None:
        try:
            _unlink_owned_path(temporary, temporary_identity)
        except FileNotFoundError:
            pass
        except BaseException as unlink_error:
            cleanup_errors.append(unlink_error)

    if cleanup_errors:
        cleanup_state_error = AtomicCommitError(
            "atomic write failed before commit and precommit cleanup was unsafe",
            committed=False,
            recovered=True,
            artifacts=_owned_artifacts(
                ((temporary, temporary_identity),)
            ),
        )
        if operation_error is not None:
            raise cleanup_state_error from operation_error
        raise cleanup_state_error from cleanup_errors[0]
    if operation_error is not None:
        raise operation_error.with_traceback(operation_traceback)


def merge_file(target, block, write=True):
    """Merge BLOCK file into TARGET file. Returns ``(changed, action)``.

    Action is 'replaced', 'appended', 'normalized' (content already
    canonical, only mixed line endings or a missing final newline were
    normalized), or 'unchanged'. The target's line-ending style and UTF-8
    BOM are preserved so that a second run is byte-identical. A file that
    mixes CRLF and LF is treated as CRLF (any CRLF present selects CRLF)
    and is stable from the second run on. Writes use a flushed same-directory
    temporary file and one atomic replace. Windows carries forward documented
    DACL/attribute/stream metadata; POSIX preserves bounded owner/group/mode/
    extended-attribute metadata. Target and block are read as no-follow
    ordinary-file snapshots. The pre-commit conflict recheck is best-effort
    rather than compare-and-swap; serialize writers externally for strict
    lost-update prevention.
    """
    target_stat, raw = _read_regular_file_snapshot(target, missing_ok=True)
    has_bom = raw.startswith(_BOM)
    text = _decode(raw, "target")
    eol = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n")

    _block_stat, block_bytes = _read_regular_file_snapshot(
        block,
        require_effective_owner=False,
        reject_encrypted=False,
    )
    block_text = _decode(block_bytes, "block").replace("\r\n", "\n")

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
            _atomic_write(target, out_bytes, target_stat, raw)
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
    except AtomicCommitError as error:
        committed = {
            True: "true",
            False: "false",
            None: "unknown",
        }[error.committed]
        print("error: %s" % error, file=sys.stderr)
        print(
            "commit-state: committed=%s recovered=%s"
            % (committed, str(bool(error.recovered)).lower()),
            file=sys.stderr,
        )
        # ascii() keeps control/bidi/non-ASCII path text from becoming active
        # terminal formatting while still identifying every retained path.
        for artifact in error.artifacts:
            print(
                "recovery-artifact: %s" % ascii(os.fspath(artifact)),
                file=sys.stderr,
            )
        return 2
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
