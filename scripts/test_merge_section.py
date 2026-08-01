#!/usr/bin/env python3
"""Self-test for merge_section.py. Standard library only:

    python scripts/test_merge_section.py

Per fixture under tests/fixtures/ it verifies that the merged output equals
expected.md byte-for-byte, that applying the same merge twice is a no-op
(apply-twice-diff-zero), and that the section heading occurs exactly once
outside leading frontmatter, code fences, and raw HTML blocks afterwards.

It also proves the skill's trap is real, not hypothetical: a fence-blind
``^##`` implementation (kept here as ``fence_blind_merge``) corrupts the
trap fixture, is not idempotent, and cuts a section short at a ``###``
subheading.

File-level cases additionally prove that a safely recoverable failed atomic
replace preserves the original bytes and removes owned temporary files,
existing POSIX permission bits survive replacement, linked targets fail
closed, and a target change visible before the final recheck is refused.
Windows recovery cases model the documented ``ReplaceFileW`` partial states
and verify that ambiguous artifacts are retained instead of guessed away.
"""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import merge_section  # noqa: E402  (import after sys.path setup)
import measure_peak_memory  # noqa: E402  (import after sys.path setup)

REPO_ROOT = SCRIPTS_DIR.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
# Discover fixtures instead of hardcoding them, so a new fixture folder is
# tested automatically (validate-oss-readiness.ps1 keeps the explicit
# required-file list).
FIXTURE_NAMES = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())


def load(fixture, name):
    """Read a fixture file as LF-normalized text (fixtures may be checked
    out with either line-ending style depending on local git settings)."""
    raw = (FIXTURES / fixture / name).read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n")


def h2_count(text, heading):
    """Count HEADING outside leading frontmatter and literal regions."""
    lines = text.split("\n")
    return len(merge_section.heading_occurrences(lines, heading))


def fence_blind_merge(document_text, block_text):
    """The folk implementation this skill exists to warn about.

    The replace range ends at the next line matching ``^##``: it does not
    track code-fence state, and ``###`` subheadings match too. It is kept in
    the test suite so the failure mode stays measured, not hypothetical.
    """
    doc = document_text.split("\n")
    if doc and doc[-1] == "":
        doc.pop()
    block = block_text.split("\n")
    while block and not block[-1].strip():
        block.pop()
    heading = block[0]
    start = None
    end = len(doc)
    for i, line in enumerate(doc):
        if start is None:
            if line.rstrip() == heading:
                start = i
        elif line.startswith("##"):  # fence-blind, and "###" matches too
            end = i
            break
    if start is None:
        return "\n".join(doc + [""] + block) + "\n"
    return "\n".join(doc[:start] + block + [""] + doc[end:]) + "\n"


class FixtureMergeTests(unittest.TestCase):
    """The correctness contract, checked on every fixture."""

    def test_fixture_discovery_found_the_known_cases(self):
        for name in (
            "append-missing-section",
            "frontmatter-heading-literal",
            "h1-boundary",
            "html-block-heading-literal",
            "replace-existing-section",
            "subheading-boundary",
            "trap-heading-inside-fence",
        ):
            self.assertIn(name, FIXTURE_NAMES)

    def test_merge_matches_expected(self):
        for fixture in FIXTURE_NAMES:
            with self.subTest(fixture=fixture):
                merged, action = merge_section.merge(
                    load(fixture, "input.md"), load(fixture, "section.md")
                )
                self.assertEqual(merged, load(fixture, "expected.md"))
                self.assertIn(action, ("replaced", "appended"))

    def test_apply_twice_is_noop(self):
        for fixture in FIXTURE_NAMES:
            with self.subTest(fixture=fixture):
                section = load(fixture, "section.md")
                once, _ = merge_section.merge(load(fixture, "input.md"), section)
                twice, action = merge_section.merge(once, section)
                self.assertEqual(twice, once)
                self.assertEqual(action, "unchanged")

    def test_heading_occurs_exactly_once_after_merge(self):
        for fixture in FIXTURE_NAMES:
            with self.subTest(fixture=fixture):
                section = load(fixture, "section.md")
                heading = section.split("\n", 1)[0].rstrip(" \t")
                merged, _ = merge_section.merge(load(fixture, "input.md"), section)
                self.assertEqual(h2_count(merged, heading), 1)


class TrapProofTests(unittest.TestCase):
    """Measured proof that the fence-blind implementation really breaks."""

    def test_fence_blind_merge_corrupts_the_trap_fixture(self):
        fixture = "trap-heading-inside-fence"
        naive = fence_blind_merge(
            load(fixture, "input.md"), load(fixture, "section.md")
        )
        expected = load(fixture, "expected.md")
        self.assertNotEqual(naive, expected)
        # The stale leftover closing fence re-opens as a fence and swallows
        # the following section: "## License" stops being a real heading...
        self.assertEqual(h2_count(naive, "## License"), 0)
        self.assertEqual(h2_count(expected, "## License"), 1)
        # ...while the fenced template line escapes its fence and becomes a
        # visible (duplicate-looking) heading.
        self.assertEqual(h2_count(naive, "## Weekly report"), 1)
        self.assertEqual(h2_count(expected, "## Weekly report"), 0)

    def test_fence_blind_merge_is_not_idempotent(self):
        fixture = "trap-heading-inside-fence"
        section = load(fixture, "section.md")
        once = fence_blind_merge(load(fixture, "input.md"), section)
        twice = fence_blind_merge(once, section)
        self.assertNotEqual(twice, once)

    def test_fence_blind_merge_cuts_at_subheading(self):
        fixture = "subheading-boundary"
        naive = fence_blind_merge(
            load(fixture, "input.md"), load(fixture, "section.md")
        )
        self.assertNotEqual(naive, load(fixture, "expected.md"))
        # The range ended at "### Build step", so the old subsections
        # survive after the new ones: the subheading now appears twice.
        self.assertEqual(naive.split("\n").count("### Build step"), 2)


class BlockValidationTests(unittest.TestCase):
    def test_block_must_start_with_h2(self):
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge("# Doc\n", "Not a heading\n\nBody.\n")

    def test_block_with_second_h2_outside_fence_is_rejected(self):
        block = "## One\n\nBody.\n\n## Two\n\nBody.\n"
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge("# Doc\n", block)

    def test_block_with_fenced_h2_is_valid(self):
        # The trap fixture's block embeds "## Weekly report" inside a fence;
        # the single-H2 invariant must not count it.
        section = load("trap-heading-inside-fence", "section.md")
        self.assertEqual(
            merge_section.validate_block(section.split("\n")),
            "## Automation notes",
        )

    def test_duplicate_headings_in_target_are_rejected(self):
        document = "## Twin\n\nA.\n\n## Twin\n\nB.\n"
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge(document, "## Twin\n\nC.\n")

    def test_unclosed_fence_in_target_is_rejected(self):
        # CommonMark runs an unclosed fence to EOF; a replace would then
        # silently rewrite the whole visually swallowed tail. The reference
        # stops and reports instead.
        document = "## Notes\n\n```\nnever closed\n\n## Next\n\nx.\n"
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge(document, "## Notes\n\nnew.\n")

    def test_unclosed_fence_in_block_is_rejected(self):
        block = "## Notes\n\n```\nnever closed\n"
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge("# Doc\n", block)

    def test_block_with_h1_is_rejected(self):
        # An H1 inside the block would become a boundary on the next run
        # and cut the maintained section in two.
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge("# Doc\n", "## Notes\n\nbody\n\n# Part\n")

    def test_block_heading_trailing_whitespace_is_normalized(self):
        merged, _ = merge_section.merge(
            "# T\n\n## Notes\n\nold.\n\n## Next\n\nkeep.\n", "## Notes  \n\nnew.\n"
        )
        self.assertIn("\n## Notes\n", merged)
        self.assertNotIn("## Notes  ", merged)

    def test_setext_block_is_rejected_before_apply_twice_can_diverge(self):
        cases = (
            ("h1", "==="),
            ("h2", "---"),
            ("indented-h1", "   ===\t"),
            ("indented-h2", "  ---  "),
        )
        documents = (
            ("append", ""),
            (
                "replace",
                "## Managed\n\nOld body.\n\n## Next\n\nKeep this section.\n",
            ),
        )
        for operation, document in documents:
            for label, underline in cases:
                with self.subTest(operation=operation, label=label):
                    block = (
                        "## Managed\n\n"
                        "Synthetic nested heading\n"
                        f"{underline}\n\n"
                        "Body that must never be written.\n"
                    )

                    # 初回appendを許すと次回replaceだけが拒否して収束しない。
                    # append/replaceともwrite前の同じ固定診断へ止める。
                    with self.assertRaisesRegex(
                        merge_section.MergeError,
                        "possible setext heading .* inside the section",
                    ):
                        merge_section.merge(document, block)

    def test_setext_block_diagnostic_does_not_reflect_utf8_content(self):
        marker = "合成入力マーカー"
        block = "## Managed\n\n%s\n---\n" % marker

        with self.assertRaises(merge_section.MergeError) as caught:
            merge_section.merge("# Synthetic document\n", block)

        self.assertIn("possible setext heading", str(caught.exception))
        self.assertNotIn(marker, str(caught.exception))

    def test_setext_literals_inside_block_literal_regions_remain_valid(self):
        blocks = (
            (
                "fence",
                "## Managed\n\n```\nSynthetic heading\n---\n```\n",
            ),
            (
                "raw-html",
                "## Managed\n\n<!--\nSynthetic heading\n---\n-->\n",
            ),
        )
        for label, block in blocks:
            with self.subTest(label=label):
                merged, action = merge_section.merge("", block)
                self.assertEqual(action, "appended")
                self.assertEqual(merged, block)


class IndentedManagedHeadingIdentityTests(unittest.TestCase):
    """管理対象 H2 の1〜3 space variantを安全側へ畳み込む契約。"""

    block = "## Managed\n\nCanonical body.\n"
    error = "indented managed H2 outside literal regions"

    def test_one_to_three_space_candidates_fail_closed(self):
        for width in (1, 2, 3):
            with self.subTest(width=width):
                trailing = " \t" if width == 3 else ""
                original = "%s## Managed%s\n\nOld body.\n" % (
                    " " * width,
                    trailing,
                )
                with self.assertRaisesRegex(merge_section.MergeError, self.error):
                    merge_section.merge(original, self.block)

    def test_fixed_error_does_not_reflect_managed_heading(self):
        marker = "SYNTHETIC-HEADING-MARKER"
        with self.assertRaises(merge_section.MergeError) as caught:
            merge_section.merge(
                "  ## %s\n" % marker,
                "## %s\n\nCanonical body.\n" % marker,
            )
        self.assertIn(self.error, str(caught.exception))
        self.assertNotIn(marker, str(caught.exception))

    def test_canonical_plus_indented_and_multiple_variants_fail_closed(self):
        documents = {
            "canonical-plus-indented": (
                "## Managed\n\nOld canonical.\n\n"
                "  ## Managed\n\nOld ambiguous copy.\n"
            ),
            "multiple-indented": (
                " ## Managed\n\nFirst.\n\n"
                "   ## Managed\n\nSecond.\n"
            ),
            "list-container-ambiguous": (
                "- Synthetic item\n"
                "  ## Managed\n\n"
                "Nested-looking body.\n"
            ),
        }
        for label, original in documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(merge_section.MergeError, self.error):
                    merge_section.merge(original, self.block)

    def test_four_spaces_tab_and_nonclosing_hash_text_are_not_same_identity(self):
        lines = (
            "    ## Managed\n"
            "\t## Managed\n"
            "## Managed#\n"
            "## Managed \\#\n"
            "## Managed ### body\n"
        )
        merged, action = merge_section.merge(lines, self.block)
        self.assertEqual(action, "appended")
        self.assertTrue(merged.startswith(lines))
        self.assertEqual(h2_count(merged, "## Managed"), 1)

    def test_indented_literal_region_lines_are_not_candidates(self):
        document = (
            "---\n"
            "example: |\n"
            "   ## Managed\n"
            "---\n\n"
            "```text\n"
            " ## Managed\n"
            "```\n\n"
            "<!--\n"
            "  ## Managed\n"
            "-->\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "appended")
        self.assertEqual(h2_count(merged, "## Managed"), 1)


class ManagedHeadingSeparatorIdentityTests(unittest.TestCase):
    """opening ``##`` 後のASCII space/tab aliasを意味上の重複として拒否する。"""

    block = "## Managed\n\nCanonical body.\n"
    alias_error = "noncanonical managed H2 separator alias outside literal regions"
    block_error = "block heading must use exactly one ASCII space before nonempty content"

    def test_noncanonical_block_separators_fail_before_append_or_replace(self):
        blocks = (
            "##  Managed\n\nBody that must not be written.\n",
            "##\tManaged\n\nBody that must not be written.\n",
            "## \tManaged\n\nBody that must not be written.\n",
            "##\t Managed  \n\nBody that must not be written.\n",
        )
        documents = (
            ("append", "# Synthetic document\n"),
            ("replace", "## Managed\n\nOld body.\n"),
        )
        for operation, document in documents:
            for block in blocks:
                with self.subTest(operation=operation, heading=repr(block.splitlines()[0])):
                    # blockの初回appendだけを許すと非canonical形が正本として固定される。
                    # append/replaceとも同じwrite前validationへ止める。
                    with self.assertRaisesRegex(
                        merge_section.MergeError,
                        self.block_error,
                    ):
                        merge_section.merge(document, block)

    def test_target_separator_aliases_fail_closed_with_indent_and_closing_hash(self):
        candidates = (
            "##  Managed",
            "##\tManaged",
            "## \t Managed",
            " ##  Managed",
            "  ##\t Managed",
            "   ## \t\tManaged",
            "##  Managed #",
            "   ##\tManaged ###\t",
        )
        for candidate in candidates:
            with self.subTest(candidate=repr(candidate)):
                document = "%s\n\nOld semantic duplicate.\n" % candidate
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    self.alias_error,
                ):
                    merge_section.merge(document, self.block)

    def test_canonical_plus_alias_and_multiple_aliases_fail_closed(self):
        documents = {
            "canonical-plus-alias": (
                "## Managed\n\nOld canonical.\n\n"
                "##\tManaged\n\nOld semantic duplicate.\n"
            ),
            "multiple-aliases": (
                " ##  Managed\n\nFirst.\n\n"
                "   ##\t Managed ####\n\nSecond.\n"
            ),
        }
        for label, document in documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    self.alias_error,
                ):
                    merge_section.merge(document, self.block)

    def test_fixed_diagnostics_do_not_reflect_utf8_heading_content(self):
        marker = "合成見出しマーカー"
        cases = (
            (
                "target",
                "##\t%s\n\nOld body.\n" % marker,
                "## %s\n\nCanonical body.\n" % marker,
                self.alias_error,
            ),
            (
                "block",
                "# Synthetic document\n",
                "##\t%s\n\nCanonical body.\n" % marker,
                self.block_error,
            ),
        )
        for label, document, block, expected_error in cases:
            with self.subTest(label=label):
                with self.assertRaises(merge_section.MergeError) as caught:
                    merge_section.merge(document, block)
                self.assertIn(expected_error, str(caught.exception))
                self.assertNotIn(marker, str(caught.exception))

    def test_literal_region_separator_aliases_remain_literals(self):
        document = (
            "---\n"
            "example: |\n"
            "  ##\tManaged\n"
            "---\n\n"
            "```text\n"
            "##  Managed\n"
            "```\n\n"
            "<!--\n"
            "   ## \t Managed ###\n"
            "-->\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "appended")
        self.assertTrue(merged.startswith(document))
        self.assertEqual(h2_count(merged, "## Managed"), 1)

    def test_exact_empty_hashtag_nonclosing_and_nonascii_contracts_remain(self):
        # exact canonical H2は従来どおりその場で置換する。
        merged, action = merge_section.merge(
            "## Managed\n\nOld body.\n",
            self.block,
        )
        self.assertEqual(action, "replaced")
        self.assertNotIn("Old body.", merged)

        # 空H2は既存の正本形として残し、末尾ASCII whitespaceだけは正規化する。
        empty, empty_action = merge_section.merge(
            "##\t \n\nOld empty body.\n",
            "##\n\nCanonical empty body.\n",
        )
        self.assertEqual(empty_action, "replaced")
        self.assertTrue(empty.startswith("##\n\nCanonical empty body.\n"))
        empty_block, empty_block_action = merge_section.merge(
            "# Synthetic document\n",
            "##\t  \n\nCanonical empty body.\n",
        )
        self.assertEqual(empty_block_action, "appended")
        self.assertTrue(empty_block.endswith("##\n\nCanonical empty body.\n"))

        # CommonMark H2でないhashtag/非ASCII separatorと、本文末尾の#は別内容。
        distinct_lines = (
            "##Managed\n"
            "##  Managed#\n"
            "##\u00a0Managed\n"
        )
        appended, appended_action = merge_section.merge(
            distinct_lines,
            self.block,
        )
        self.assertEqual(appended_action, "appended")
        self.assertTrue(appended.startswith(distinct_lines))
        self.assertTrue(appended.endswith(self.block))

        with self.assertRaisesRegex(
            merge_section.MergeError,
            "block must start with an H2 heading line",
        ):
            merge_section.merge("", "##\u00a0Managed\n\nNot an H2.\n")

        # canonical separator後の非ASCII whitespaceは本文そのものであり、
        # Pythonの広いstripで落としたりseparator aliasへ畳み込んだりしない。
        for whitespace in ("\u00a0", "\u2003", "\u000c", "\u000b"):
            with self.subTest(non_ascii=repr(whitespace)):
                non_ascii_block = (
                    "## %sManaged\n\nCanonical non-ASCII content.\n" % whitespace
                )
                non_ascii_merged, non_ascii_action = merge_section.merge(
                    "# Synthetic document\n",
                    non_ascii_block,
                )
                self.assertEqual(non_ascii_action, "appended")
                self.assertTrue(non_ascii_merged.endswith(non_ascii_block))


class ClosingHashManagedHeadingIdentityTests(unittest.TestCase):
    """CommonMarkのclosing-hash variantを同じ管理対象H2として安全側に扱う。"""

    block = "## Managed\n\nCanonical body.\n"
    alias_error = "closing-hash managed H2 alias outside literal regions"
    block_error = "block heading must use plain form without a closing hash sequence"

    def test_closing_hash_candidates_fail_closed(self):
        cases = (
            ("## Managed #", self.block),
            ("## Managed ##   ", self.block),
            (" ## Managed ###", self.block),
            ("   ## Managed ########\t", self.block),
            ("## C# ##", "## C#\n\nCanonical body.\n"),
        )
        for candidate, block in cases:
            with self.subTest(candidate=candidate):
                original = "%s\n\nOld body.\n" % candidate
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    self.alias_error,
                ):
                    merge_section.merge(original, block)

    def test_canonical_plus_alias_and_multiple_aliases_fail_closed(self):
        documents = {
            "canonical-plus-alias": (
                "## Managed\n\nOld canonical.\n\n"
                "## Managed ##\n\nOld semantic duplicate.\n"
            ),
            "multiple-aliases": (
                "## Managed #\n\nFirst.\n\n"
                "  ## Managed ####\n\nSecond.\n"
            ),
        }
        for label, original in documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    self.alias_error,
                ):
                    merge_section.merge(original, self.block)

    def test_fixed_alias_error_does_not_reflect_managed_heading(self):
        marker = "SYNTHETIC-CLOSING-HASH-MARKER"
        with self.assertRaises(merge_section.MergeError) as caught:
            merge_section.merge(
                "## %s ##\n" % marker,
                "## %s\n\nCanonical body.\n" % marker,
            )
        self.assertIn(self.alias_error, str(caught.exception))
        self.assertNotIn(marker, str(caught.exception))

    def test_literal_region_candidates_are_not_aliases(self):
        document = (
            "---\n"
            "example: |\n"
            "  ## Managed ##\n"
            "---\n\n"
            "```text\n"
            "## Managed ###\n"
            "```\n\n"
            "<!--\n"
            " ## Managed #\n"
            "-->\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "appended")
        self.assertTrue(merged.startswith(document))
        self.assertEqual(h2_count(merged, "## Managed"), 1)

    def test_closing_hash_block_heading_is_rejected(self):
        for heading in ("## Managed #", "## Managed ###   "):
            with self.subTest(heading=heading):
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    self.block_error,
                ):
                    merge_section.merge(
                        "# Synthetic document\n",
                        "%s\n\nCanonical body.\n" % heading,
                    )

    def test_nonclosing_content_hash_in_block_remains_supported(self):
        merged, action = merge_section.merge(
            "# Synthetic document\n",
            "## C#\n\nCanonical body.\n",
        )
        self.assertEqual(action, "appended")
        self.assertTrue(merged.endswith("## C#\n\nCanonical body.\n"))


class AsciiWhitespaceGrammarTests(unittest.TestCase):
    """CommonMarkのblock文法でspace/tab以外を勝手にtrimしない契約。"""

    block = "## Managed\n\nCanonical body.\n"
    non_ascii_whitespace = {
        "nbsp": "\u00a0",
        "em-space": "\u2003",
        "form-feed": "\u000c",
        "vertical-tab": "\u000b",
    }

    def test_ascii_helpers_accept_only_space_and_tab(self):
        for text in ("", " ", "\t", " \t "):
            with self.subTest(text=repr(text)):
                self.assertTrue(merge_section._is_ascii_blank(text))
        for label, whitespace in self.non_ascii_whitespace.items():
            with self.subTest(label=label):
                self.assertFalse(merge_section._is_ascii_blank(whitespace))
                self.assertEqual(
                    merge_section._rstrip_ascii_whitespace(
                        "content%s" % whitespace
                    ),
                    "content%s" % whitespace,
                )

    def test_non_ascii_whitespace_after_target_heading_is_not_plain(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            for indent in ("", "  "):
                with self.subTest(label=label, indent=len(indent)):
                    original = (
                        "# Synthetic document\n\n"
                        "%s## Managed%s\n\nDifferent section body.\n"
                        % (indent, whitespace)
                    )
                    merged, action = merge_section.merge(original, self.block)
                    self.assertEqual(action, "appended")
                    self.assertTrue(merged.startswith(original))
                    self.assertTrue(merged.endswith(self.block))

    def test_public_occurrence_scan_does_not_trim_unicode_whitespace(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            with self.subTest(label=label):
                self.assertEqual(
                    merge_section.heading_occurrences(
                        ["## Managed%s" % whitespace],
                        "## Managed",
                    ),
                    [],
                )

    def test_non_ascii_whitespace_after_block_heading_is_not_trimmed(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            with self.subTest(label=label):
                original = "## Managed\n\nExisting plain section.\n"
                block = "## Managed%s\n\nDifferent section body.\n" % whitespace
                merged, action = merge_section.merge(original, block)
                self.assertEqual(action, "appended")
                self.assertTrue(merged.startswith(original))
                self.assertTrue(merged.endswith(block))

    def test_non_ascii_whitespace_only_trailing_line_is_preserved(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            with self.subTest(label=label, location="target"):
                original = "# Synthetic document\n\n%s\n" % whitespace
                merged, action = merge_section.merge(original, self.block)
                self.assertEqual(action, "appended")
                self.assertTrue(merged.startswith(original))
                self.assertIn("%s\n\n## Managed\n" % whitespace, merged)

            with self.subTest(label=label, location="block"):
                block = "## Managed\n\nCanonical body.\n%s\n" % whitespace
                merged, action = merge_section.merge(
                    "# Synthetic document\n",
                    block,
                )
                self.assertEqual(action, "appended")
                self.assertTrue(merged.endswith(block))

    def test_non_ascii_whitespace_setext_heading_is_rejected(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            with self.subTest(label=label):
                original = (
                    "## Managed\n\nOld body.\n\n"
                    "%s\n---\n\nProtected tail.\n\n"
                    "## Next\n\nKeep.\n" % whitespace
                )
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    "possible setext heading",
                ):
                    merge_section.merge(original, self.block)

    def test_non_ascii_whitespace_does_not_close_target_fence(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            for fence_kind, opener, closer in (
                ("backtick", "```text", "```"),
                ("tilde", "~~~ text", "~~~"),
            ):
                with self.subTest(label=label, fence=fence_kind):
                    original = (
                        "## Managed\n\nOld body.\n\n"
                        "%s\n"
                        "literal\n"
                        "%s%s\n"
                        "# Protected inside the unclosed fence\n"
                        % (opener, closer, whitespace)
                    )
                    with self.assertRaisesRegex(
                        merge_section.MergeError,
                        "target ends inside an unclosed code fence",
                    ):
                        merge_section.merge(original, self.block)

    def test_non_ascii_whitespace_does_not_close_block_fence(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            for fence_kind, opener, closer in (
                ("backtick", "```text", "```"),
                ("tilde", "~~~ text", "~~~"),
            ):
                with self.subTest(label=label, fence=fence_kind):
                    block = (
                        "## Managed\n\n"
                        "%s\n"
                        "literal\n"
                        "%s%s\n" % (opener, closer, whitespace)
                    )
                    with self.assertRaisesRegex(
                        merge_section.MergeError,
                        "block ends inside an unclosed code fence",
                    ):
                        merge_section.merge("# Synthetic document\n", block)

    def test_non_ascii_whitespace_after_hashes_is_not_a_closing_sequence(self):
        for label, whitespace in self.non_ascii_whitespace.items():
            with self.subTest(label=label, location="block"):
                block = "## Managed ##%s\n\nDifferent section body.\n" % whitespace
                merged, action = merge_section.merge(
                    "# Synthetic document\n",
                    block,
                )
                self.assertEqual(action, "appended")
                self.assertTrue(merged.endswith(block))

            with self.subTest(label=label, location="target"):
                original = (
                    "## Managed ##%s\n\nDifferent section body.\n" % whitespace
                )
                merged, action = merge_section.merge(original, self.block)
                self.assertEqual(action, "appended")
                self.assertTrue(merged.startswith(original))


class BoundaryHardeningTests(unittest.TestCase):
    """Boundary rules beyond the folk ``^##[^#]`` form, each measured."""

    def test_yaml_frontmatter_heading_literals_are_ignored(self):
        for closer in ("---", "..."):
            with self.subTest(closer=closer):
                document = (
                    "---\n"
                    "title: Synthetic guide\n"
                    "\n"
                    "example: |\n"
                    "  ```text\n"
                    "## Managed\n"
                    "owner: example\n"
                    f"{closer}\n"
                    "\n"
                    "# Document\n"
                    "\n"
                    "Intro.\n"
                )
                block = "## Managed\n\nCanonical body.\n"

                # frontmatter 内の疑似 H2 は置換対象ではない。正本節は文書末尾へ
                # 追記し、2回目は同じ bytes に収束しなければならない。
                merged, action = merge_section.merge(document, block)
                self.assertEqual(action, "appended")
                self.assertTrue(merged.startswith(document))
                self.assertIn("owner: example\n" + closer, merged)
                twice, second_action = merge_section.merge(merged, block)
                self.assertEqual(twice, merged)
                self.assertEqual(second_action, "unchanged")
                self.assertEqual(h2_count(merged, "## Managed"), 1)

    def test_toml_frontmatter_heading_literal_is_ignored(self):
        document = (
            "+++\n"
            'title = "Synthetic guide"\n'
            "## Managed\n"
            'owner = "example"\n'
            "+++\n"
            "\n"
            "# Document\n"
            "\n"
            "Intro.\n"
        )
        block = "## Managed\n\nCanonical body.\n"

        merged, action = merge_section.merge(document, block)
        self.assertEqual(action, "appended")
        self.assertTrue(merged.startswith(document))
        self.assertIn('owner = "example"\n+++', merged)
        twice, second_action = merge_section.merge(merged, block)
        self.assertEqual(twice, merged)
        self.assertEqual(second_action, "unchanged")

    def test_frontmatter_after_closer_replaces_real_heading(self):
        document = (
            "---\n"
            "title: Synthetic guide\n"
            "## Managed\n"
            "---\n"
            "\n"
            "# Document\n"
            "\n"
            "## Managed\n"
            "\n"
            "Old body.\n"
            "\n"
            "## Next\n"
            "\n"
            "Keep.\n"
        )
        merged, action = merge_section.merge(
            document, "## Managed\n\nCanonical body.\n"
        )
        self.assertEqual(action, "replaced")
        self.assertIn("title: Synthetic guide\n## Managed\n---", merged)
        self.assertNotIn("Old body.", merged)
        self.assertIn("## Next\n\nKeep.", merged)

    def test_unclosed_frontmatter_is_rejected(self):
        for opener in ("---", "+++"):
            with self.subTest(opener=opener):
                document = (
                    f"{opener}\n"
                    "title: Synthetic guide\n"
                    "## Managed\n"
                    "owner: example\n"
                )
                with self.assertRaisesRegex(
                    merge_section.MergeError, "unclosed .*frontmatter"
                ):
                    merge_section.merge(
                        document, "## Managed\n\nCanonical body.\n"
                    )

    def test_frontmatter_delimiters_require_exact_lines(self):
        # 空白付き closer は別行として扱い、曖昧な frontmatter を静かに
        # 置換しない。opener も完全一致でなければ通常 Markdown として扱う。
        for opener, loose_closer, kind in (
            ("---", "--- ", "YAML"),
            ("+++", "+++ ", "TOML"),
        ):
            with self.subTest(kind=kind, delimiter="closer"):
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    f"unclosed {kind} frontmatter",
                ):
                    merge_section.merge(
                        f"{opener}\ntitle: Synthetic guide\n{loose_closer}\n"
                        "# Document\n",
                        "## Managed\n\nCanonical body.\n",
                    )

        document = (
            "--- \n"
            "\n"
            "## Managed\n"
            "\n"
            "Old body.\n"
            "\n"
            "---\n"
            "\n"
            "# Next\n"
            "\n"
            "Keep.\n"
        )
        merged, action = merge_section.merge(
            document, "## Managed\n\nCanonical body.\n"
        )
        self.assertEqual(action, "replaced")
        self.assertTrue(merged.startswith("--- \n"))
        self.assertIn("# Next\n\nKeep.", merged)

    def test_nonleading_thematic_break_and_heading_boundaries_are_unchanged(self):
        document = (
            "# Document\n"
            "\n"
            "---\n"
            "\n"
            "## Managed\n"
            "\n"
            "Old body.\n"
            "\n"
            "# Next part\n"
            "\n"
            "Keep.\n"
        )
        merged, action = merge_section.merge(
            document, "## Managed\n\nCanonical body.\n"
        )
        self.assertEqual(action, "replaced")
        self.assertIn("# Document\n\n---\n", merged)
        self.assertIn("# Next part\n\nKeep.", merged)
        self.assertNotIn("Old body.", merged)

    def test_indented_h2_ends_the_section(self):
        doc = "## Notes\n\nold.\n\n  ## Indented\n\nindent body.\n\n## Next\n\nx.\n"
        merged, _ = merge_section.merge(doc, "## Notes\n\nnew.\n")
        self.assertIn("  ## Indented", merged)
        self.assertIn("indent body.", merged)

    def test_hash_without_space_is_not_a_boundary(self):
        # CommonMark: "##hashtag" is a paragraph line, not a heading. It is
        # part of the old body, so a replace consumes it and the real next
        # section survives.
        doc = "## Notes\n\nold.\n##hashtag style line\n\n## Next\n\nkeep.\n"
        merged, _ = merge_section.merge(doc, "## Notes\n\nnew.\n")
        self.assertNotIn("##hashtag", merged)
        self.assertIn("## Next", merged)
        self.assertIn("keep.", merged)

    def test_backtick_info_string_does_not_open_a_fence(self):
        # CommonMark: a backtick fence cannot carry backticks in its info
        # string, so this line is body text and "## Next" stays a boundary.
        doc = "## Notes\n\nold.\n```a`b\nstill body.\n\n## Next\n\nkeep.\n"
        merged, _ = merge_section.merge(doc, "## Notes\n\nnew.\n")
        self.assertIn("## Next", merged)
        self.assertIn("keep.", merged)
        self.assertNotIn("still body.", merged)

    def test_stray_fence_after_non_fence_info_line_is_still_unclosed(self):
        # The same document with a later bare ``` line: that one does open a
        # fence, never closes, and the unclosed-fence guard refuses.
        doc = "## Notes\n\nold.\n```a`b\n## Next\nkeep.\n```\n\n## Last\n\nx.\n"
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge(doc, "## Notes\n\nnew.\n")

    def test_setext_heading_inside_span_is_rejected(self):
        doc = (
            "## Notes\n\nold.\n\nNext Section\n------------\n\nkeep under "
            "setext.\n\n## Last\n\nx.\n"
        )
        with self.assertRaises(merge_section.MergeError):
            merge_section.merge(doc, "## Notes\n\nnew.\n")


class HtmlBlockBoundaryTests(unittest.TestCase):
    """CommonMark raw HTML blocks must not expose heading-looking literals."""

    block = "## Notes\n\nCanonical body.\n"
    expected = (
        "## Notes\n\nCanonical body.\n\n"
        "## Next\n\nKeep this real section.\n"
    )

    def _assert_html_body_is_replaced(self, opener, closer):
        document = (
            "## Notes\n\nOld body.\n\n"
            f"{opener}\n"
            "# Hidden title\n"
            "## Hidden section\n"
            f"{closer}\n"
            "Old tail.\n\n"
            "## Next\n\nKeep this real section.\n"
        )

        # HTML 内の疑似境界では止まらず、HTML と旧 tail を含む管理対象節全体を
        # 次の実 H2 直前まで置換することを全文一致で固定する。
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_explicit_end_html_types_hide_pseudo_headings(self):
        cases = {
            "script": ("<script type=\"text/plain\">", "</script>"),
            "style": ("<style>", "</style>"),
            "pre": ("<pre>", "</pre>"),
            "textarea": ("<textarea>", "</textarea>"),
            "comment": ("<!--", "-->"),
            "processing-instruction": ("<?synthetic", "?>"),
            "declaration": ("<!SYNTHETIC", ">"),
            "cdata": ("<![CDATA[", "]]>"),
        }
        for kind, (opener, closer) in cases.items():
            with self.subTest(kind=kind):
                self._assert_html_body_is_replaced(opener, closer)

    def test_type_one_is_case_insensitive_and_accepts_any_family_end_tag(self):
        # CommonMark type 1 の終端は opener と同名でなくてもよい。この少し
        # 意外な規則を固定し、独自 HTML tag-stack へ変質させない。
        self._assert_html_body_is_replaced("<SCRIPT>", "</TeXtArEa>")

    def test_explicit_end_html_accepts_zero_to_three_leading_spaces(self):
        for indent in ("", " ", "  ", "   "):
            with self.subTest(spaces=len(indent)):
                self._assert_html_body_is_replaced(
                    indent + "<script>",
                    indent + "</script>",
                )

    def test_explicit_end_html_can_open_and_close_on_one_line(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "<script>const synthetic = '## literal';</script>\n"
            "Old tail.\n\n"
            "## Next\n\nKeep this real section.\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_blank_line_terminated_html_types_hide_pseudo_headings(self):
        cases = {
            # type 6 は tag 全体が未完成でも、列挙 tag + 空白で開始する。
            "type-6": "<DIV class",
            # type 7 は block-level list 外の complete tag 単独行で開始する。
            "type-7": "<Synthetic-Widget data-mode='preview'>",
        }
        for kind, opener in cases.items():
            with self.subTest(kind=kind):
                document = (
                    "## Notes\n\nOld body.\n\n"
                    f"{opener}\n"
                    "# Hidden title\n"
                    "## Hidden section\n"
                    "</Synthetic-Widget>\n"
                    "\n"
                    "Old tail.\n\n"
                    "## Next\n\nKeep this real section.\n"
                )
                merged, action = merge_section.merge(document, self.block)
                self.assertEqual(action, "replaced")
                self.assertEqual(merged, self.expected)

    def test_type_seven_does_not_interrupt_a_paragraph(self):
        document = (
            "## Notes\n\n"
            "Old paragraph without a separating blank line.\n"
            "<Synthetic-Widget>\n"
            "## Next\n\nKeep this real section.\n"
        )

        # type 7 の complete tag でも段落途中では inline HTML である。
        # 直後の実 H2 を HTML に飲み込まず、境界として残す。
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_reference_definition_then_equals_html_context_fails_closed(self):
        document = (
            "[ref]: /synthetic\n"
            "===\n"
            "<Synthetic-Widget>\n"
            "## Managed\n"
            "</Synthetic-Widget>\n"
        )
        block = "## Managed\n\nCanonical body.\n"

        # CommonMark 0.31.2 Example 216 の系: 有効な reference definition
        # だけの paragraph に続く === は setext 化できず、=== 自体が新しい
        # paragraph text になる。簡易走査では definition の完全妥当性を証明
        # できないため、type 7 を開始して同名H2を隠す推測はせず拒否する。
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "ambiguous setext context after a possible link reference "
            "definition",
        ):
            merge_section.merge(document, block)

    def test_multiline_reference_label_then_equals_html_context_fails_closed(self):
        document = (
            "[\n"
            "foo\n"
            "]: /synthetic\n"
            "===\n"
            "<Synthetic-Widget>\n"
            "## Managed\n"
            "</Synthetic-Widget>\n"
        )
        block = "## Managed\n\nCanonical body.\n"

        # CommonMark 0.31.2 Example 208 型では link label 自体が複数行に
        # またがれる。閉じ行まで possible definition 状態を維持しないと、
        # === 後の type 7 が実H2を隠し、同名節の重複appendを許してしまう。
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "ambiguous setext context after a possible link reference "
            "definition",
        ):
            merge_section.merge(document, block)

    def test_escaped_line_end_multiline_reference_fails_closed(self):
        document = (
            "[foo\\\n"
            "bar]: /synthetic\n"
            "===\n"
            "<Synthetic-Widget>\n"
            "## Managed\n"
            "</Synthetic-Widget>\n"
        )
        block = "## Managed\n\nCanonical body.\n"

        # 行末の単一 backslash も複数行labelを無効化しない。開始判定から
        # 漏れると type 7 が実H2を隠し、同名節を末尾へ重複appendしてしまう。
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "ambiguous setext context after a possible link reference "
            "definition",
        ):
            merge_section.merge(document, block)

    def test_closed_bracket_text_is_not_a_multiline_reference_label(self):
        lines = (
            "[Ordinary bracketed text]\n"
            "===\n"
            "## Candidate\n"
        ).splitlines()

        # 同じ行で閉じた通常の bracket text は複数行labelの開始ではない。
        # 保守判定を必要以上に広げず、後続の実ATX見出しを走査できる。
        self.assertEqual(merge_section.boundary_indices(lines), [2])

    def test_multiline_bracket_text_without_colon_releases_reference_state(self):
        lines = (
            "[Ordinary\n"
            "bracketed text]\n"
            "===\n"
            "## Candidate\n"
        ).splitlines()

        # 最初の未escape ``]`` の直後が colon でなければ definition には
        # なれない。通常の複数行 setext text を曖昧入力として過剰拒否しない。
        self.assertEqual(merge_section.boundary_indices(lines), [3])

    def test_escaped_closing_bracket_is_not_a_reference_definition(self):
        lines = (
            "[Escaped\\]: ordinary text\n"
            "===\n"
            "## Candidate\n"
        ).splitlines()

        # ``\]`` は label closer ではない。表面的な ``]:`` だけで
        # definition候補にすると通常setext textを過剰拒否してしまう。
        self.assertEqual(merge_section.boundary_indices(lines), [2])

    def test_type_seven_after_link_reference_definition_stays_in_paragraph(self):
        lines = (
            "[ref]: /synthetic\n"
            "<Synthetic-Widget>\n"
            "## Candidate\n"
            "</Synthetic-Widget>\n"
        ).splitlines()

        # 空行なしの単純形は CommonMark 0.31.2 と同じく type 7 が段落を
        # 割り込めない。compound === caseを安全化しても、この実H2を隠さない。
        self.assertEqual(merge_section.boundary_indices(lines), [2])

    def test_type_seven_can_start_after_an_atx_subheading(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "### Child\n"
            "<Synthetic-Widget>\n"
            "## Hidden section\n"
            "</Synthetic-Widget>\n"
            "\n"
            "Old tail.\n\n"
            "## Next\n\nKeep this real section.\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_closing_tags_can_start_type_six_and_type_seven_blocks(self):
        for kind, opener in (
            ("type-6", "</DIV>"),
            ("type-7", "</Synthetic-Widget>"),
        ):
            with self.subTest(kind=kind):
                document = (
                    "## Notes\n\nOld body.\n\n"
                    f"{opener}\n"
                    "## Hidden section\n"
                    "\n"
                    "Old tail.\n\n"
                    "## Next\n\nKeep this real section.\n"
                )
                merged, action = merge_section.merge(document, self.block)
                self.assertEqual(action, "replaced")
                self.assertEqual(merged, self.expected)

    def test_blank_line_html_types_may_end_at_eof(self):
        for kind, opener in (
            ("type-6", "<div>"),
            ("type-7", "<Synthetic-Widget>"),
        ):
            with self.subTest(kind=kind):
                document = (
                    "# Document\n\n"
                    f"{opener}\n"
                    "## Hidden section\n"
                )
                block = "## Managed\n\nCanonical body.\n"
                expected = document + "\n" + block

                # type 6/7 の EOF は仕様上の正常終端。追記時に入る空行が
                # HTML と新しい管理対象 H2 を分離し、2回目は同じ bytes になる。
                merged, action = merge_section.merge(document, block)
                self.assertEqual(action, "appended")
                self.assertEqual(merged, expected)
                twice, second_action = merge_section.merge(merged, block)
                self.assertEqual(second_action, "unchanged")
                self.assertEqual(twice, merged)

    def test_ambiguous_type_seven_after_container_fails_closed(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "- Synthetic list item\n"
            "<Synthetic-Widget>\n"
            "## Hidden or real depending on container parsing\n"
        )
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "ambiguous raw HTML block type 7 context",
        ):
            merge_section.merge(document, self.block)

    def test_tag_prefixes_and_trailing_text_do_not_open_wrong_html_type(self):
        for label, ordinary_line in (
            ("type-1-prefix", "<scripture> trailing text"),
            ("type-6-prefix", "<divine> trailing text"),
            ("type-7-trailing-text", "<Synthetic-Widget> trailing text"),
        ):
            with self.subTest(label=label):
                document = (
                    "## Notes\n\nOld body.\n\n"
                    f"{ordinary_line}\n"
                    "Still ordinary paragraph text.\n"
                    "## Next\n\nKeep this real section.\n"
                )
                merged, action = merge_section.merge(document, self.block)
                self.assertEqual(action, "replaced")
                self.assertEqual(merged, self.expected)

    def test_unicode_casefold_characters_are_not_ascii_html_tag_grammar(self):
        invalid_html_lines = (
            # Python IGNORECASE without ASCII also folds these four code points
            # into [A-Z]/[a-z]. CommonMark tag grammar is ASCII-only.
            "<İtag>",
            "<ıtag>",
            "<ſtag>",
            "<Ktag>",
            # Literal tag-name regexes must not inherit the same Unicode fold.
            "<ſcript>",
            "<ſection>",
            "<İframe>",
            "<ıframe>",
            # Type 7 attribute names are ASCII-only at every position too.
            "<custom Key='synthetic'>",
            "<custom data-K='synthetic'>",
        )
        for invalid_line in invalid_html_lines:
            with self.subTest(codepoints=invalid_line.encode("unicode_escape")):
                document = (
                    "## Notes\n\nOld body.\n\n"
                    f"{invalid_line}\n"
                    "## Next\n\nKeep this real section.\n"
                )
                merged, action = merge_section.merge(document, self.block)
                self.assertEqual(action, "replaced")
                self.assertEqual(merged, self.expected)

    def test_unicode_casefold_end_tag_does_not_close_ascii_type_one(self):
        for invalid_closer in ("</ſcript>", "</scrİpt>", "</scrıpt>"):
            with self.subTest(
                codepoints=invalid_closer.encode("unicode_escape")
            ):
                document = (
                    "## Notes\n\nOld body.\n\n"
                    "<script>\n"
                    "## Hidden section\n"
                    f"{invalid_closer}\n"
                    "## Next\n\nKeep this apparent section.\n"
                )

                # ASCII <script> remains unclosed. Treating a Unicode-folded
                # closer as ASCII would bypass the mutation-safety guard.
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    "unclosed raw HTML block type 1",
                ):
                    merge_section.merge(document, self.block)

    def test_four_space_html_opener_is_indented_code_not_raw_html(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "    <script>\n"
            "    ## Hidden indented-code heading\n"
            "\n"
            "## Next\n\nKeep this real section.\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_fence_delimiter_inside_html_does_not_open_a_fence(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "<div>\n"
            "```\n"
            "## Hidden section\n"
            "\n"
            "Old tail.\n\n"
            "## Next\n\nKeep this real section.\n"
        )

        # type 6 HTML は空行まで続くため、内部の bare fence は状態遷移しない。
        # これを fence-first の独立走査に戻すと unclosed fence と誤判定する。
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_html_opener_inside_fence_does_not_open_html(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "```html\n"
            "<script>\n"
            "## Hidden section\n"
            "```\n"
            "\n"
            "Old tail.\n\n"
            "## Next\n\nKeep this real section.\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_setext_like_underline_inside_html_is_ignored(self):
        document = (
            "## Notes\n\nOld body.\n\n"
            "<div>\n"
            "Synthetic title\n"
            "---\n"
            "## Hidden section\n"
            "\n"
            "Old tail.\n\n"
            "## Next\n\nKeep this real section.\n"
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, self.expected)

    def test_html_like_content_inside_frontmatter_does_not_leak_state(self):
        document = (
            "---\n"
            "title: Synthetic guide\n"
            "example: |\n"
            "  <script>\n"
            "## Hidden metadata heading\n"
            "---\n"
            "\n"
            "## Notes\n\nOld body.\n\n"
            "## Next\n\nKeep this real section.\n"
        )
        expected = (
            "---\n"
            "title: Synthetic guide\n"
            "example: |\n"
            "  <script>\n"
            "## Hidden metadata heading\n"
            "---\n"
            "\n"
            + self.expected
        )
        merged, action = merge_section.merge(document, self.block)
        self.assertEqual(action, "replaced")
        self.assertEqual(merged, expected)

    def test_unclosed_explicit_end_html_types_fail_closed_in_target(self):
        openers = {
            "type 1": "<script>",
            "type 2": "<!--",
            "type 3": "<?synthetic",
            "type 4": "<!SYNTHETIC",
            "type 5": "<![CDATA[",
        }
        for kind, opener in openers.items():
            with self.subTest(kind=kind):
                document = (
                    "## Notes\n\nOld body.\n\n"
                    f"{opener}\n"
                    "## Hidden section\n"
                )
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    f"unclosed raw HTML block {kind}",
                ):
                    merge_section.merge(document, self.block)

    def test_unclosed_explicit_end_html_type_fails_closed_in_block(self):
        block = "## Notes\n\n<script>\n## Hidden section\n"
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "unclosed raw HTML block type 1",
        ):
            merge_section.merge("# Document\n", block)


class WindowsCommitRecoveryTests(unittest.TestCase):
    """Deterministic state-machine tests for the Windows commit helper."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _files(self):
        return {path.name for path in self.dir.iterdir()}

    def _existing_commit(self):
        target = self.dir / "target.md"
        original = b"# Doc\n\nold.\n"
        replacement = b"# Doc\n\nnew.\n"
        target.write_bytes(original)
        if os.name == "nt":
            descriptor, temporary = merge_section._open_atomic_temporary(target)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
                temporary_stat = os.fstat(stream.fileno())
        else:
            temporary = self.dir / ".target.md.pending"
            temporary.write_bytes(replacement)
            temporary_stat = temporary.lstat()
        return (
            target,
            temporary,
            target.lstat(),
            original,
            temporary_stat,
            replacement,
        )

    def _missing_commit(self):
        target = self.dir / "target.md"
        replacement = b"# Doc\n\nnew.\n"
        if os.name == "nt":
            descriptor, temporary = merge_section._open_atomic_temporary(
                target
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
                temporary_stat = os.fstat(stream.fileno())
        else:
            temporary = self.dir / ".target.md.pending"
            temporary.write_bytes(replacement)
            temporary_stat = temporary.lstat()
        return target, temporary, temporary_stat, replacement

    def test_uninspectable_artifact_does_not_mask_structured_state(self):
        candidate = self.dir / ".target.md.recovery-backup"

        for failure in (
            PermissionError("synthetic ACL denial"),
            KeyboardInterrupt("synthetic inspection interrupt"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=failure,
                ):
                    error = merge_section._windows_commit_state_error(
                        "synthetic partial commit",
                        committed=None,
                        recovered=False,
                        paths=(candidate,),
                    )

                self.assertIsNone(error.committed)
                self.assertFalse(error.recovered)
                self.assertEqual(error.artifacts, (candidate,))
                self.assertIn(ascii(os.fspath(candidate)), str(error))

    def test_windows_no_replace_interrupt_after_commit_is_reconciled(self):
        target, temporary, temporary_stat, replacement = (
            self._missing_commit()
        )

        def move_then_interrupt(source, destination):
            source.replace(destination)
            raise KeyboardInterrupt("synthetic post-move interrupt")

        with mock.patch.object(
            merge_section,
            "_move_file_windows_no_replace_raw",
            side_effect=move_then_interrupt,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._move_new_windows_file(
                    target,
                    temporary,
                    temporary_stat,
                    replacement,
                )

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(target.read_bytes(), replacement)
        self.assertFalse(temporary.exists())

    def test_windows_no_replace_precommit_interrupt_cleans_temporary(self):
        target, temporary, temporary_stat, replacement = (
            self._missing_commit()
        )

        with mock.patch.object(
            merge_section,
            "_move_file_windows_no_replace_raw",
            side_effect=KeyboardInterrupt("synthetic pre-move interrupt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                merge_section._move_new_windows_file(
                    target,
                    temporary,
                    temporary_stat,
                    replacement,
                )

        self.assertFalse(target.exists())
        self.assertFalse(temporary.exists())

    def test_windows_precommit_verification_interrupt_is_structured(self):
        target, temporary, temporary_stat, replacement = (
            self._missing_commit()
        )

        with mock.patch.object(
            merge_section,
            "_verified_replacement_snapshot",
            side_effect=KeyboardInterrupt("synthetic verification interrupt"),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._move_new_windows_file(
                    target,
                    temporary,
                    temporary_stat,
                    replacement,
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (temporary,))
        self.assertTrue(temporary.exists())

    def test_windows_backup_setup_interrupt_is_structured_and_cleaned(self):
        target = self.dir / "target.md"

        with mock.patch.object(
            os,
            "fsync",
            side_effect=KeyboardInterrupt("synthetic backup fsync interrupt"),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._open_windows_backup_placeholder(target)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, ())
        self.assertEqual(self._files(), set())

    def test_windows_commit_success_removes_verified_backup(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_success(target_path, temporary_path, backup_path):
            # ReplaceFileW 成功状態を、旧 target -> backup、
            # replacement -> target の順で同一 directory 上に再現する。
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            temporary_path.replace(target_path)
            return 0

        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            side_effect=replace_success,
        ):
            merge_section._commit_existing_windows(
                target,
                temporary,
                target_stat,
                original,
                temporary_stat,
                replacement,
            )

        self.assertEqual(target.read_bytes(), replacement)
        self.assertFalse(temporary.exists())
        self.assertFalse(captured["backup"].exists())
        self.assertEqual(self._files(), {"target.md"})

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_windows_commit_rejects_fifo_swap_before_private_temp_read(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        original_open = merge_section._open_regular_read_descriptor

        def open_then_replace_with_fifo(
            path,
            missing_ok=False,
            reject_encrypted=True,
        ):
            descriptor = original_open(
                path,
                missing_ok=missing_ok,
                reject_encrypted=reject_encrypted,
            )
            if path == temporary:
                temporary.unlink()
                os.mkfifo(temporary)
            return descriptor

        with mock.patch.object(
            merge_section,
            "_open_regular_read_descriptor",
            side_effect=open_then_replace_with_fifo,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (temporary,))
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(stat.S_ISFIFO(temporary.lstat().st_mode))

    def test_windows_commit_success_retains_backup_when_cleanup_fails(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_success(target_path, temporary_path, backup_path):
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            temporary_path.replace(target_path)
            return 0

        with (
            mock.patch.object(
                merge_section,
                "_replace_file_windows_raw",
                side_effect=replace_success,
            ),
            mock.patch.object(
                merge_section,
                "_unlink_owned_path",
                side_effect=OSError("synthetic backup cleanup failure"),
            ),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (captured["backup"],))
        self.assertEqual(target.read_bytes(), replacement)
        self.assertEqual(captured["backup"].read_bytes(), original)
        self.assertFalse(temporary.exists())

    def test_windows_commit_success_retains_recovery_when_target_is_unverified(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def inconsistent_success(target_path, _temporary_path, backup_path):
            # API の success と実測 state が食い違えば、原本 backup を
            # 消さず replacement temp と一緒に回復材料として保持する。
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            target_path.write_bytes(b"# Unexpected\n")
            return 0

        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            side_effect=inconsistent_success,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(
            set(caught.exception.artifacts),
            {captured["backup"], temporary},
        )
        self.assertEqual(target.read_bytes(), b"# Unexpected\n")
        self.assertEqual(captured["backup"].read_bytes(), original)
        self.assertEqual(temporary.read_bytes(), replacement)

    def test_windows_error_1176_cleans_owned_unmoved_artifacts(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            return_value=1176,
        ):
            with self.assertRaises(OSError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertEqual(caught.exception.errno, 1176)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(temporary.exists())
        self.assertEqual(self._files(), {"target.md"})

    def test_windows_nonpartial_failures_preserve_original_and_cleanup(self):
        cases = (
            ("error-1175", 1175, OSError),
            ("unknown-error", 4321, OSError),
            ("raw-exception", RuntimeError("synthetic loader failure"), RuntimeError),
        )
        for name, result, expected_error in cases:
            with self.subTest(name=name):
                case_dir = self.dir / name
                case_dir.mkdir()
                target = case_dir / "target.md"
                original = b"# Doc\n\nold.\n"
                replacement = b"# Doc\n\nnew.\n"
                target.write_bytes(original)
                if os.name == "nt":
                    descriptor, temporary = (
                        merge_section._open_atomic_temporary(target)
                    )
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(replacement)
                        stream.flush()
                        os.fsync(stream.fileno())
                        temporary_stat = os.fstat(stream.fileno())
                else:
                    temporary = case_dir / ".target.md.pending"
                    temporary.write_bytes(replacement)
                    temporary_stat = temporary.lstat()

                patch_arguments = (
                    {"side_effect": result}
                    if isinstance(result, Exception)
                    else {"return_value": result}
                )
                with mock.patch.object(
                    merge_section,
                    "_replace_file_windows_raw",
                    **patch_arguments,
                ):
                    with self.assertRaises(expected_error):
                        merge_section._commit_existing_windows(
                            target,
                            temporary,
                            target.lstat(),
                            original,
                            temporary_stat,
                            replacement,
                        )

                self.assertEqual(target.read_bytes(), original)
                self.assertFalse(temporary.exists())
                self.assertEqual(
                    {path.name for path in case_dir.iterdir()},
                    {"target.md"},
                )

    def test_windows_error_1176_retains_artifacts_when_target_changed(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_then_report_1176(target_path, _temporary_path, backup_path):
            # API結果と観測状態が食い違う場合は、documented state を
            # 推測せず target・temp・placeholder をそのまま残す。
            captured["backup"] = backup_path
            target_path.write_bytes(b"# External\n")
            return 1176

        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            side_effect=replace_then_report_1176,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertIsNone(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(
            set(caught.exception.artifacts),
            {captured["backup"], temporary},
        )
        self.assertEqual(target.read_bytes(), b"# External\n")
        self.assertEqual(temporary.read_bytes(), replacement)
        self.assertTrue(captured["backup"].exists())

    def test_windows_error_reports_verified_replacement_as_committed(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_then_report_error(target_path, temporary_path, backup_path):
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            temporary_path.replace(target_path)
            return 4321

        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            side_effect=replace_then_report_error,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (captured["backup"],))
        self.assertEqual(target.read_bytes(), replacement)
        self.assertEqual(captured["backup"].read_bytes(), original)

    def test_windows_error_reports_unresolved_state_as_unknown(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def remove_target_then_report_error(
            target_path,
            _temporary_path,
            backup_path,
        ):
            captured["backup"] = backup_path
            target_path.unlink()
            return 4321

        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            side_effect=remove_target_then_report_error,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertIsNone(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(
            set(caught.exception.artifacts),
            {captured["backup"], temporary},
        )
        self.assertFalse(target.exists())
        self.assertEqual(temporary.read_bytes(), replacement)

    def test_windows_error_1177_restores_without_replacement(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_partial(target_path, _temporary_path, backup_path):
            # ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 は旧 target が backup
            # へ移動済み、replacement が temp 名に残る状態を表す。
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            return 1177

        def restore_no_replace(source, destination):
            self.assertFalse(destination.exists())
            source.replace(destination)
            return 0

        with (
            mock.patch.object(
                merge_section,
                "_replace_file_windows_raw",
                side_effect=replace_partial,
            ),
            mock.patch.object(
                merge_section,
                "_move_file_windows_no_replace_raw",
                side_effect=restore_no_replace,
            ),
        ):
            with self.assertRaises(OSError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertEqual(caught.exception.errno, 1177)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(temporary.exists())
        self.assertFalse(captured["backup"].exists())
        self.assertEqual(self._files(), {"target.md"})

    def test_windows_error_1177_restore_interrupt_is_reconciled(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_partial(target_path, _temporary_path, backup_path):
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            return 1177

        def restore_then_interrupt(source, destination):
            source.replace(destination)
            raise KeyboardInterrupt("synthetic post-restore interrupt")

        with (
            mock.patch.object(
                merge_section,
                "_replace_file_windows_raw",
                side_effect=replace_partial,
            ),
            mock.patch.object(
                merge_section,
                "_move_file_windows_no_replace_raw",
                side_effect=restore_then_interrupt,
            ),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, ())
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(temporary.exists())
        self.assertFalse(captured["backup"].exists())

    def test_windows_error_1177_does_not_overwrite_new_target(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}

        def replace_partial_then_create(
            target_path,
            _temporary_path,
            backup_path,
        ):
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            target_path.write_bytes(b"# External\n")
            return 1177

        with mock.patch.object(
            merge_section,
            "_replace_file_windows_raw",
            side_effect=replace_partial_then_create,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertFalse(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(
            set(caught.exception.artifacts),
            {captured["backup"], temporary},
        )
        self.assertEqual(target.read_bytes(), b"# External\n")
        self.assertEqual(captured["backup"].read_bytes(), original)
        self.assertEqual(temporary.read_bytes(), replacement)

    def test_windows_error_1177_target_inspection_failure_is_structured(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )
        captured = {}
        armed = {"value": False}
        original_inspect = merge_section._inspect_target

        def replace_partial(target_path, _temporary_path, backup_path):
            captured["backup"] = backup_path
            target_path.replace(backup_path)
            armed["value"] = True
            return 1177

        def inspect_with_denial(path, *args, **kwargs):
            if armed["value"] and path == target:
                raise PermissionError("synthetic target inspection denial")
            return original_inspect(path, *args, **kwargs)

        with (
            mock.patch.object(
                merge_section,
                "_replace_file_windows_raw",
                side_effect=replace_partial,
            ),
            mock.patch.object(
                merge_section,
                "_inspect_target",
                side_effect=inspect_with_denial,
            ),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertIsNone(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(
            set(caught.exception.artifacts),
            {captured["backup"], temporary},
        )
        self.assertFalse(target.exists())
        self.assertEqual(captured["backup"].read_bytes(), original)

    def test_windows_error_1177_retains_temp_when_recovery_cleanup_fails(self):
        target, temporary, target_stat, original, temporary_stat, replacement = (
            self._existing_commit()
        )

        def replace_partial(target_path, _temporary_path, backup_path):
            target_path.replace(backup_path)
            return 1177

        def restore_no_replace(source, destination):
            source.replace(destination)
            return 0

        original_unlink_owned = merge_section._unlink_owned_path

        def fail_temporary_cleanup(path, expected_stat):
            if path == temporary:
                raise OSError("synthetic temporary cleanup failure")
            return original_unlink_owned(path, expected_stat)

        with (
            mock.patch.object(
                merge_section,
                "_replace_file_windows_raw",
                side_effect=replace_partial,
            ),
            mock.patch.object(
                merge_section,
                "_move_file_windows_no_replace_raw",
                side_effect=restore_no_replace,
            ),
            mock.patch.object(
                merge_section,
                "_unlink_owned_path",
                side_effect=fail_temporary_cleanup,
            ),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._commit_existing_windows(
                    target,
                    temporary,
                    target_stat,
                    original,
                    temporary_stat,
                    replacement,
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (temporary,))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(temporary.read_bytes(), replacement)

    @unittest.skipUnless(os.name == "nt", "ownership transfer is Windows-only")
    def test_atomic_write_does_not_delete_temp_after_commit_ownership_transfer(self):
        target, unused, target_stat, original, _temporary_stat, replacement = (
            self._existing_commit()
        )
        unused.unlink()
        captured = {}
        events = []

        def assert_stable_target_snapshot(
            target_path,
            observed_stat,
            observed_bytes,
        ):
            # このテストの責務はcommit helperへ所有権を渡した後のcleanupだけ。
            # 実filesystemのtimestamp揺らぎを混ぜず、同じsnapshotを検証した事実を
            # 引数と順序で固定してからcommit境界へ進める。
            self.assertEqual(target_path, target)
            self.assertIs(observed_stat, target_stat)
            self.assertEqual(observed_bytes, original)
            events.append("target-guard")

        def fail_after_ownership_transfer(
            _target,
            temporary,
            _target_stat,
            _original_bytes,
            _replacement_stat,
            _replacement_bytes,
        ):
            captured["temporary"] = temporary
            events.append("commit")
            raise merge_section.AtomicCommitError(
                "synthetic ambiguous commit",
                committed=False,
                recovered=False,
                artifacts=(temporary,),
            )

        with (
            mock.patch.object(
                merge_section,
                "_assert_target_unchanged",
                side_effect=assert_stable_target_snapshot,
            ) as target_guard,
            mock.patch.object(
                merge_section,
                "_commit_temporary",
                side_effect=fail_after_ownership_transfer,
            ) as commit,
        ):
            with self.assertRaises(merge_section.AtomicCommitError):
                merge_section._atomic_write(
                    target,
                    replacement,
                    target_stat,
                    original,
                )

        target_guard.assert_called_once_with(target, target_stat, original)
        commit.assert_called_once()
        self.assertEqual(events, ["target-guard", "commit"])
        self.assertTrue(captured["temporary"].exists())
        self.assertEqual(captured["temporary"].read_bytes(), replacement)
        self.assertEqual(target.read_bytes(), original)

    def test_target_metadata_only_drift_is_rejected_before_commit(self):
        target, unused, target_stat, original, _temporary_stat, replacement = (
            self._existing_commit()
        )
        unused.unlink()
        original_target_guard = merge_section._assert_target_unchanged

        def drift_target_then_assert(
            target_path,
            observed_stat,
            observed_bytes,
        ):
            self.assertEqual(target_path, target)
            self.assertIs(observed_stat, target_stat)
            self.assertEqual(observed_bytes, original)

            # Python 3.14もtimestampの実精度はfilesystem依存とするため、
            # 微小差やsleepには頼らない。FATの2秒粒度でも区別できる固定mtimeを
            # commit直前に設定し、実fingerprint差を確認して本番guardを通す。
            stable_mtime_ns = 978_307_200_000_000_000
            os.utime(
                target_path,
                ns=(observed_stat.st_atime_ns, stable_mtime_ns),
            )
            drifted_stat = target_path.lstat()
            self.assertEqual(target_path.read_bytes(), observed_bytes)
            self.assertNotEqual(
                merge_section._stat_fingerprint(drifted_stat),
                merge_section._stat_fingerprint(observed_stat),
            )
            return original_target_guard(
                target_path,
                observed_stat,
                observed_bytes,
            )

        with (
            mock.patch.object(
                merge_section,
                "_assert_target_unchanged",
                side_effect=drift_target_then_assert,
            ) as target_guard,
            mock.patch.object(
                merge_section,
                "_commit_temporary",
            ) as commit,
        ):
            with self.assertRaisesRegex(
                merge_section.MergeError,
                "target metadata changed during merge",
            ):
                merge_section._atomic_write(
                    target,
                    replacement,
                    target_stat,
                    original,
                )

        target_guard.assert_called_once_with(target, target_stat, original)
        commit.assert_not_called()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self._files(), {"target.md"})


class FileLevelTests(unittest.TestCase):
    """Byte-level guarantees: EOL and BOM preservation, CLI exit codes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    @unittest.skipUnless(os.name == "nt", "Windows DACLs are Windows-only")
    def _assert_windows_owner_only_dacl(self, path):
        import ctypes
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
        get_security = advapi32.GetNamedSecurityInfoW
        get_security.argtypes = (
            wintypes.LPWSTR,
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
        local_free = ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        ).LocalFree
        local_free.argtypes = (wintypes.HLOCAL,)
        local_free.restype = wintypes.HLOCAL

        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        dacl_security_information = 0x00000004
        se_file_object = 1
        error_code = get_security(
            str(path),
            se_file_object,
            dacl_security_information,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        self.assertEqual(error_code, 0)
        try:
            control = wintypes.WORD()
            revision = wintypes.DWORD()
            self.assertTrue(
                get_control(
                    descriptor,
                    ctypes.byref(control),
                    ctypes.byref(revision),
                )
            )
            se_dacl_protected = 0x1000
            self.assertTrue(control.value & se_dacl_protected)

            information = AclSizeInformation()
            acl_size_information = 2
            self.assertTrue(
                get_acl_information(
                    dacl,
                    ctypes.byref(information),
                    ctypes.sizeof(information),
                    acl_size_information,
                )
            )
            self.assertEqual(information.AceCount, 2)

            expected_sids = []
            try:
                for text in ("S-1-5-18", "S-1-3-4"):
                    sid = wintypes.LPVOID()
                    self.assertTrue(convert_sid(text, ctypes.byref(sid)))
                    expected_sids.append(sid)

                matched = [False, False]
                file_all_access = 0x001F01FF
                access_allowed_ace_type = 0
                for index in range(information.AceCount):
                    ace_pointer = wintypes.LPVOID()
                    self.assertTrue(
                        get_ace(dacl, index, ctypes.byref(ace_pointer))
                    )
                    ace = ctypes.cast(
                        ace_pointer,
                        ctypes.POINTER(AccessAllowedAce),
                    ).contents
                    self.assertEqual(
                        ace.Header.AceType,
                        access_allowed_ace_type,
                    )
                    self.assertEqual(ace.Header.AceFlags, 0)
                    self.assertEqual(ace.Mask, file_all_access)
                    sid_pointer = ctypes.c_void_p(
                        ace_pointer.value
                        + AccessAllowedAce.SidStart.offset
                    )
                    sid_matches = [
                        bool(equal_sid(sid_pointer, expected))
                        for expected in expected_sids
                    ]
                    self.assertEqual(sum(sid_matches), 1)
                    matched[sid_matches.index(True)] = True
                self.assertEqual(matched, [True, True])
            finally:
                for sid in expected_sids:
                    local_free(sid)
        finally:
            local_free(descriptor)

    @unittest.skipUnless(os.name == "nt", "Windows DACLs are Windows-only")
    def _add_windows_everyone_read_ace(self, path):
        """Broaden a fixture DACL so the final private-temp check must fail."""

        import ctypes
        from ctypes import wintypes

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
        set_file_security = advapi32.SetFileSecurityW
        set_file_security.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
        )
        set_file_security.restype = wintypes.BOOL

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        local_free = kernel32.LocalFree
        local_free.argtypes = (wintypes.HLOCAL,)
        local_free.restype = wintypes.HLOCAL

        descriptor = wintypes.LPVOID()
        self.assertTrue(
            convert_descriptor(
                "D:P(A;;FA;;;SY)(A;;FA;;;OW)(A;;GR;;;WD)",
                1,
                ctypes.byref(descriptor),
                None,
            )
        )
        try:
            dacl_security_information = 0x00000004
            self.assertTrue(
                set_file_security(
                    str(path),
                    dacl_security_information,
                    descriptor,
                )
            )
        finally:
            local_free(descriptor)

    @unittest.skipUnless(os.name == "nt", "Windows DACLs are Windows-only")
    def _windows_dacl_sddl(self, path):
        """Return the canonical DACL text for before/after comparison."""

        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        get_security = advapi32.GetNamedSecurityInfoW
        get_security.argtypes = (
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
        )
        get_security.restype = wintypes.DWORD
        to_string = (
            advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        )
        to_string.argtypes = (
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        )
        to_string.restype = wintypes.BOOL

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        local_free = kernel32.LocalFree
        local_free.argtypes = (wintypes.HLOCAL,)
        local_free.restype = wintypes.HLOCAL

        descriptor = wintypes.LPVOID()
        dacl_security_information = 0x00000004
        se_file_object = 1
        error_code = get_security(
            str(path),
            se_file_object,
            dacl_security_information,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        self.assertEqual(error_code, 0)
        text = wintypes.LPWSTR()
        try:
            self.assertTrue(
                to_string(
                    descriptor,
                    1,
                    dacl_security_information,
                    ctypes.byref(text),
                    None,
                )
            )
            return text.value
        finally:
            if text:
                local_free(text)
            local_free(descriptor)

    def test_atomic_temporary_starts_owner_only(self):
        target = self.dir / "target.md"
        descriptor, temporary = merge_section._open_atomic_temporary(target)
        descriptor_open = True
        try:
            if os.name == "nt":
                self._assert_windows_owner_only_dacl(temporary)
            else:
                self.assertEqual(
                    stat.S_IMODE(os.fstat(descriptor).st_mode),
                    0o600,
                )
            os.close(descriptor)
            descriptor_open = False
            with temporary.open("ab") as reopened:
                reopened.write(b"owner-can-reopen")
        finally:
            if descriptor_open:
                os.close(descriptor)
            temporary.unlink()

    @unittest.skipIf(os.name == "nt", "POSIX umask is POSIX-only")
    def test_posix_private_mode_is_exact_under_restrictive_umask(self):
        target = self.dir / "target.md"
        previous_umask = os.umask(0o777)
        descriptor = None
        temporary = None
        try:
            descriptor, temporary = merge_section._open_atomic_temporary(
                target
            )
        finally:
            os.umask(previous_umask)
        try:
            self.assertEqual(
                stat.S_IMODE(os.fstat(descriptor).st_mode),
                0o600,
            )
        finally:
            os.close(descriptor)
            temporary.unlink()

    @unittest.skipIf(os.name == "nt", "POSIX fchmod is POSIX-only")
    def test_posix_private_mode_interrupt_reports_artifact(self):
        target = self.dir / "target.md"

        with mock.patch.object(
            os,
            "fchmod",
            side_effect=KeyboardInterrupt("synthetic fchmod interrupt"),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._open_atomic_temporary(target)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(len(caught.exception.artifacts), 1)
        temporary = caught.exception.artifacts[0]
        self.assertTrue(temporary.exists())
        temporary.unlink()

    def test_initial_temporary_identity_failure_reports_artifact(self):
        target = self.dir / "target.md"
        replacement = b"## Notes\n\nnew.\n"

        for failure in (
            OSError("synthetic fstat failure"),
            KeyboardInterrupt("synthetic fstat interrupt"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    os,
                    "fstat",
                    side_effect=failure,
                ):
                    with self.assertRaises(
                        merge_section.AtomicCommitError
                    ) as caught:
                        merge_section._atomic_write(
                            target,
                            replacement,
                            None,
                            b"",
                        )

                self.assertFalse(caught.exception.committed)
                self.assertTrue(caught.exception.recovered)
                self.assertEqual(len(caught.exception.artifacts), 1)
                temporary = caught.exception.artifacts[0]
                self.assertTrue(temporary.exists())
                self.assertEqual(temporary.read_bytes(), b"")
                if os.name == "nt":
                    self._assert_windows_owner_only_dacl(temporary)
                else:
                    self.assertEqual(
                        stat.S_IMODE(temporary.lstat().st_mode),
                        0o600,
                    )
                temporary.unlink()

    def test_outer_close_failure_does_not_mask_identity_error(self):
        target = self.dir / "target.md"
        replacement = b"## Notes\n\nnew.\n"
        original_close = os.close

        def close_then_interrupt(descriptor):
            original_close(descriptor)
            raise KeyboardInterrupt("cleanup close interrupt")

        with (
            mock.patch.object(
                os,
                "fstat",
                side_effect=OSError("primary identity failure"),
            ),
            mock.patch.object(
                os,
                "close",
                side_effect=close_then_interrupt,
            ),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._atomic_write(
                    target,
                    replacement,
                    None,
                    b"",
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertIsInstance(
            caught.exception.__cause__,
            merge_section.AtomicCommitError,
        )
        self.assertIn(
            "identity could not be recorded",
            str(caught.exception.__cause__),
        )
        self.assertEqual(len(caught.exception.artifacts), 1)
        temporary = caught.exception.artifacts[0]
        self.assertTrue(temporary.exists())
        temporary.unlink()

    def test_outer_unlink_failure_does_not_mask_primary_error(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        replacement = b"# Doc\n\n## Notes\n\nnew.\n"
        target_stat, original = merge_section._read_regular_file_snapshot(
            target,
            max_bytes=merge_section._MAX_TARGET_BYTES,
            oversize_error=merge_section._TARGET_OVERSIZE_ERROR,
        )
        original_open = merge_section._open_atomic_temporary
        captured = {}

        def capture_open(target_path):
            descriptor, temporary = original_open(target_path)
            captured["temporary"] = temporary
            return descriptor, temporary

        with (
            mock.patch.object(
                merge_section,
                "_open_atomic_temporary",
                side_effect=capture_open,
            ),
            mock.patch.object(
                merge_section,
                "_assert_target_unchanged",
                side_effect=merge_section.MergeError(
                    "primary precommit failure"
                ),
            ),
            mock.patch.object(
                merge_section,
                "_unlink_owned_path",
                side_effect=OSError("cleanup unlink failure"),
            ),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section._atomic_write(
                    target,
                    replacement,
                    target_stat,
                    original,
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertIsInstance(
            caught.exception.__cause__,
            merge_section.MergeError,
        )
        self.assertEqual(
            str(caught.exception.__cause__),
            "primary precommit failure",
        )
        self.assertEqual(
            caught.exception.artifacts,
            (captured["temporary"],),
        )
        self.assertTrue(captured["temporary"].exists())
        captured["temporary"].unlink()

    @unittest.skipUnless(os.name == "nt", "Windows descriptors are Windows-only")
    def test_windows_descriptor_setup_failure_reports_private_artifact(self):
        import msvcrt

        target = self.dir / "target.md"
        for failure in (
            OSError("synthetic descriptor failure"),
            KeyboardInterrupt("synthetic descriptor interrupt"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    msvcrt,
                    "open_osfhandle",
                    side_effect=failure,
                ):
                    with self.assertRaises(
                        merge_section.AtomicCommitError
                    ) as caught:
                        merge_section._open_windows_atomic_temporary(target)

                self.assertFalse(caught.exception.committed)
                self.assertTrue(caught.exception.recovered)
                self.assertEqual(len(caught.exception.artifacts), 1)
                temporary = caught.exception.artifacts[0]
                self.assertTrue(temporary.exists())
                self._assert_windows_owner_only_dacl(temporary)
                temporary.unlink()

    def test_temporary_metadata_drift_is_rejected_before_commit(self):
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_commit = merge_section._commit_temporary
        captured = {}

        def mutate_then_commit(
            target_path,
            temporary,
            target_stat,
            original_bytes,
            replacement_stat,
            replacement_bytes,
        ):
            # Mutate policy metadata without touching inode identity or bytes.
            # Windows DACLs are not represented in os.stat, so that path also
            # exercises the explicit security-descriptor verification.
            if os.name == "nt":
                self._add_windows_everyone_read_ace(temporary)
            else:
                temporary.chmod(0o644)
            captured["temporary"] = temporary
            return original_commit(
                target_path,
                temporary,
                target_stat,
                original_bytes,
                replacement_stat,
                replacement_bytes,
            )

        with mock.patch.object(
            merge_section,
            "_commit_temporary",
            side_effect=mutate_then_commit,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertFalse(target.exists())
        self.assertTrue(captured["temporary"].exists())
        captured["temporary"].unlink()

    @unittest.skipUnless(
        os.name == "nt",
        "Windows file attributes are Windows-only",
    )
    def test_windows_temporary_attribute_drift_is_rejected_before_commit(self):
        import ctypes
        from ctypes import wintypes

        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_commit = merge_section._commit_temporary
        captured = {}

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_attributes = kernel32.GetFileAttributesW
        get_attributes.argtypes = (wintypes.LPCWSTR,)
        get_attributes.restype = wintypes.DWORD
        set_attributes = kernel32.SetFileAttributesW
        set_attributes.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
        set_attributes.restype = wintypes.BOOL
        invalid_attributes = 0xFFFFFFFF
        hidden = 0x00000002

        def mutate_then_commit(
            target_path,
            temporary,
            target_stat,
            original_bytes,
            replacement_stat,
            replacement_bytes,
        ):
            attributes = get_attributes(str(temporary))
            self.assertNotEqual(attributes, invalid_attributes)
            self.assertTrue(
                set_attributes(str(temporary), attributes | hidden)
            )
            captured["temporary"] = temporary
            return original_commit(
                target_path,
                temporary,
                target_stat,
                original_bytes,
                replacement_stat,
                replacement_bytes,
            )

        with mock.patch.object(
            merge_section,
            "_commit_temporary",
            side_effect=mutate_then_commit,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertFalse(target.exists())
        self.assertTrue(captured["temporary"].exists())
        self.assertTrue(
            get_attributes(str(captured["temporary"])) & hidden
        )
        captured["temporary"].unlink()

    @unittest.skipUnless(os.name == "nt", "Windows DACLs are Windows-only")
    def test_existing_windows_temporary_dacl_drift_is_rejected(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_commit = merge_section._commit_temporary
        captured = {}

        def mutate_then_commit(
            target_path,
            temporary,
            target_stat,
            original_bytes,
            replacement_stat,
            replacement_bytes,
        ):
            self._add_windows_everyone_read_ace(temporary)
            captured["temporary"] = temporary
            return original_commit(
                target_path,
                temporary,
                target_stat,
                original_bytes,
                replacement_stat,
                replacement_bytes,
            )

        with mock.patch.object(
            merge_section,
            "_commit_temporary",
            side_effect=mutate_then_commit,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(target.read_bytes(), b"# Doc\n\n## Notes\n\nold.\n")
        self.assertTrue(captured["temporary"].exists())
        captured["temporary"].unlink()

    @unittest.skipUnless(os.name == "nt", "Windows ownership is Windows-only")
    def test_read_only_block_does_not_require_effective_token_owner(self):
        target = self.dir / "missing.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")

        with mock.patch.object(
            merge_section,
            "_assert_windows_descriptor_owned_by_effective_owner",
            side_effect=AssertionError("block owner check must stay disabled"),
        ) as owner_check:
            changed, action = merge_section.merge_file(
                target,
                block,
                write=False,
            )

        self.assertTrue(changed)
        self.assertEqual(action, "appended")
        owner_check.assert_not_called()

    def test_outer_cleanup_does_not_unlink_a_swapped_name(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_open = merge_section._open_atomic_temporary
        captured = {}

        def capture_open(target_path):
            descriptor, temporary = original_open(target_path)
            captured["temporary"] = temporary
            return descriptor, temporary

        def swap_then_fail(*_args):
            owned_artifact = self.dir / "owned-artifact.md"
            captured["temporary"].replace(owned_artifact)
            captured["owned_artifact"] = owned_artifact
            captured["temporary"].write_bytes(b"foreign object")
            raise merge_section.MergeError("synthetic pre-commit failure")

        with (
            mock.patch.object(
                merge_section,
                "_open_atomic_temporary",
                side_effect=capture_open,
            ),
            mock.patch.object(
                merge_section,
                "_assert_target_unchanged",
                side_effect=swap_then_fail,
            ),
        ):
            with self.assertRaises(
                merge_section.AtomicCommitError
            ) as caught:
                merge_section.merge_file(target, block)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertIsInstance(
            caught.exception.__cause__,
            merge_section.MergeError,
        )
        self.assertEqual(
            str(caught.exception.__cause__),
            "synthetic pre-commit failure",
        )
        self.assertEqual(caught.exception.artifacts, ())
        self.assertEqual(captured["temporary"].read_bytes(), b"foreign object")
        self.assertIn(b"new.", captured["owned_artifact"].read_bytes())
        self.assertEqual(target.read_bytes(), b"# Doc\n\n## Notes\n\nold.\n")

    def test_final_output_exact_limit_is_written_and_idempotent(self):
        block_bytes = b"## Notes\n\nnew.\n"
        block = self._write("section.md", block_bytes)
        output_limit = merge_section._MAX_OUTPUT_BYTES

        # 既存末尾newlineとblockの間へ入る1 byte separatorまで逆算し、
        # final raw outputをproductionの8 MiB境界へexactに合わせる。
        target_length = output_limit - 1 - len(block_bytes)
        prefix = b"# Doc\n\n"
        original = prefix + (b"x" * (target_length - len(prefix) - 1)) + b"\n"
        self.assertEqual(len(original) + 1 + len(block_bytes), output_limit)
        target = self._write("target.md", original)

        changed, action = merge_section.merge_file(target, block)

        self.assertTrue(changed)
        self.assertEqual(action, "appended")
        exact_output = target.read_bytes()
        self.assertEqual(len(exact_output), output_limit)
        self.assertTrue(exact_output.endswith(b"\n\n" + block_bytes))

        # 自身が生成したexact-limit targetは次回inputとして受理でき、
        # byte-identicalなno-opにならなければclosure/idempotence違反となる。
        with mock.patch.object(merge_section, "_atomic_write") as atomic_write:
            changed_again, action_again = merge_section.merge_file(target, block)
        self.assertFalse(changed_again)
        self.assertEqual(action_again, "unchanged")
        atomic_write.assert_not_called()
        self.assertEqual(target.read_bytes(), exact_output)

    def test_cli_rejects_append_output_limit_plus_one_without_writing(self):
        marker = "非公開出力標識"
        original = b"# Doc\n"
        block_bytes = b"## Notes\n\nnew.\n"
        target = self._write("%s.md" % marker, original)
        block = self._write("section.md", block_bytes)
        expected_length = len(original) + 1 + len(block_bytes)

        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                target.write_bytes(original)
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        merge_section,
                        "_MAX_OUTPUT_BYTES",
                        expected_length - 1,
                    ),
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: merged output exceeds the supported byte limit\n",
                )
                self.assertNotIn(marker, stderr.getvalue())
                self.assertNotIn(block.name, stderr.getvalue())
                temporary_open.assert_not_called()
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)
                self.assertEqual(
                    sorted(path.name for path in self.dir.iterdir()),
                    sorted((block.name, target.name)),
                )

    def test_bom_crlf_longer_replacement_obeys_final_output_limit(self):
        fixture = "replace-existing-section"
        bom = b"\xef\xbb\xbf"
        original = bom + load(fixture, "input.md").replace(
            "\n", "\r\n"
        ).encode("utf-8")
        block_bytes = load(fixture, "section.md").encode("utf-8")
        expected = bom + load(fixture, "expected.md").replace(
            "\n", "\r\n"
        ).encode("utf-8")
        self.assertGreater(len(expected), len(original))
        target = self._write("target.md", original)
        block = self._write("section.md", block_bytes)

        # BOMとCRLF展開後のfinal raw bytesがexactならwriteと2回目no-opを許可する。
        with mock.patch.object(
            merge_section,
            "_MAX_OUTPUT_BYTES",
            len(expected),
        ):
            changed, action = merge_section.merge_file(target, block)
            changed_again, action_again = merge_section.merge_file(target, block)
        self.assertTrue(changed)
        self.assertEqual(action, "replaced")
        self.assertFalse(changed_again)
        self.assertEqual(action_again, "unchanged")
        self.assertEqual(target.read_bytes(), expected)

        # 同じreplacementをfinal limit+1へ落とすと、通常/--checkの双方で
        # temp作成前に固定診断へfail closedし、target/blockを保持する。
        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                target.write_bytes(original)
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        merge_section,
                        "_MAX_OUTPUT_BYTES",
                        len(expected) - 1,
                    ),
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: merged output exceeds the supported byte limit\n",
                )
                temporary_open.assert_not_called()
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)
                self.assertEqual(
                    sorted(path.name for path in self.dir.iterdir()),
                    sorted((block.name, target.name)),
                )

    def test_mixed_eol_normalization_rejects_output_limit_plus_one(self):
        marker = "非公開正規化標識"
        original = b"# T\r\n\n## Notes\nnew.\n"
        block_bytes = b"## Notes\nnew.\n"
        expected = b"# T\r\n\r\n## Notes\r\nnew.\r\n"
        target = self._write("%s.md" % marker, original)
        block = self._write("section.md", block_bytes)
        self.assertGreater(len(expected), len(original))

        # managed sectionの内容が同じでも、mixed EOLをCRLFへ統一するとraw
        # bytesは増える。normalizationだけのlimit+1もwrite/temp前に拒否する。
        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                target.write_bytes(original)
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        merge_section,
                        "_MAX_OUTPUT_BYTES",
                        len(expected) - 1,
                    ),
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: merged output exceeds the supported byte limit\n",
                )
                self.assertNotIn(marker, stderr.getvalue())
                self.assertNotIn(block.name, stderr.getvalue())
                temporary_open.assert_not_called()
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)
                self.assertEqual(
                    sorted(path.name for path in self.dir.iterdir()),
                    sorted((block.name, target.name)),
                )

    def test_input_and_commit_snapshot_reads_are_bounded_at_exact_limits(self):
        original = b"# Doc\n\n## Notes\n\nold.\n"
        block_bytes = b"## Notes\n\nnew.\n"
        target = self._write("target.md", original)
        block = self._write("section.md", block_bytes)
        original_fdopen = os.fdopen
        read_sizes = []

        class TrackingReadStream:
            """Proxy regular reads so every requested byte count stays visible."""

            def __init__(self, stream):
                self._stream = stream

            def read(self, size=-1):
                read_sizes.append(size)
                return self._stream.read(size)

            def fileno(self):
                return self._stream.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *_exc_info):
                self._stream.close()
                return False

        def tracking_fdopen(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            if mode == "rb":
                return TrackingReadStream(stream)
            return stream

        # target初回、block初回、commit直前target再読をexact-limitで通し、
        # いずれもlimit+1以外の無制限readへ退行していないことを測る。
        with (
            mock.patch.object(
                merge_section,
                "_MAX_TARGET_BYTES",
                len(original),
            ),
            mock.patch.object(
                merge_section,
                "_MAX_BLOCK_BYTES",
                len(block_bytes),
            ),
            mock.patch.object(os, "fdopen", side_effect=tracking_fdopen),
        ):
            changed, action = merge_section.merge_file(target, block)

        self.assertTrue(changed)
        self.assertEqual(action, "replaced")
        self.assertEqual(
            read_sizes[:3],
            [
                len(original) + 1,
                len(block_bytes) + 1,
                len(original) + 1,
            ],
        )
        self.assertTrue(read_sizes)
        self.assertNotIn(-1, read_sizes)
        self.assertIn(b"new.", target.read_bytes())

    def test_raw_newline_budget_accepts_exact_limit_and_rejects_limit_plus_one(self):
        limit = 4

        # LF byteをraw段階で数えるため、BOMやCRLFでもlogical separator数は
        # 変わらない。exactは受理し、同じ固定診断でlimit+1だけを拒否する。
        for raw in (
            b"\n" * limit,
            merge_section._BOM + (b"line\r\n" * limit) + b"tail",
        ):
            with self.subTest(raw_prefix=raw[:3]):
                self.assertIsNone(
                    merge_section._assert_raw_newline_budget(
                        raw,
                        max_newlines=limit,
                        oversize_error="newline limit crossed",
                    )
                )

        with self.assertRaisesRegex(
            merge_section.MergeError,
            "^newline limit crossed$",
        ):
            merge_section._assert_raw_newline_budget(
                b"\n" * (limit + 1),
                max_newlines=limit,
                oversize_error="newline limit crossed",
            )

        # 実定数のexact境界もhelperだけで固定し、million-line文書をmergeして
        # CI memoryを不必要に増やさずoff-by-oneを検出する。
        self.assertEqual(merge_section._MAX_TARGET_NEWLINES, 1_000_000)
        self.assertEqual(merge_section._MAX_BLOCK_NEWLINES, 250_000)
        self.assertEqual(
            merge_section._MAX_OUTPUT_NEWLINES,
            merge_section._MAX_TARGET_NEWLINES,
        )
        merge_section._assert_raw_newline_budget(
            b"\n" * merge_section._MAX_TARGET_NEWLINES,
            max_newlines=merge_section._MAX_TARGET_NEWLINES,
            oversize_error=merge_section._TARGET_NEWLINE_OVERSIZE_ERROR,
        )
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "^target exceeds the supported newline count limit$",
        ):
            merge_section._assert_raw_newline_budget(
                b"\n" * (merge_section._MAX_TARGET_NEWLINES + 1),
                max_newlines=merge_section._MAX_TARGET_NEWLINES,
                oversize_error=merge_section._TARGET_NEWLINE_OVERSIZE_ERROR,
            )

        # 100,000行の通常長paragraphはbyte/newline両budget内に十分収まり、
        # 大規模だが高密度ではない既存Markdownを新境界が拒否しない。
        representative = b"ordinary markdown paragraph for compatibility\n" * 100_000
        self.assertLess(len(representative), merge_section._MAX_TARGET_BYTES)
        self.assertLess(
            representative.count(b"\n"),
            merge_section._MAX_TARGET_NEWLINES,
        )
        merge_section._assert_raw_newline_budget(
            representative,
            max_newlines=merge_section._MAX_TARGET_NEWLINES,
            oversize_error=merge_section._TARGET_NEWLINE_OVERSIZE_ERROR,
        )

    def test_cli_rejects_target_newline_limit_plus_one_before_decode_or_write(self):
        marker = "非公開ターゲット行数標識"
        original = b"x\nx\nx\n"
        block_bytes = b"## Notes\n\nnew.\n"
        target = self._write("%s.md" % marker, original)
        block = self._write("section.md", block_bytes)

        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(merge_section, "_MAX_TARGET_NEWLINES", 2),
                    mock.patch.object(merge_section, "_decode") as decode,
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: target exceeds the supported newline count limit\n",
                )
                self.assertNotIn(marker, stderr.getvalue())
                decode.assert_not_called()
                temporary_open.assert_not_called()
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_block_newline_limit_plus_one_before_merge_or_write(self):
        marker = "非公開ブロック行数標識"
        original = b"# Doc\n\n## Notes\n\nold.\n"
        block_bytes = ("## Notes\n\n%s\nextra\n" % marker).encode("utf-8")
        target = self._write("target.md", original)
        block = self._write("%s.md" % marker, block_bytes)

        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(merge_section, "_MAX_BLOCK_NEWLINES", 3),
                    mock.patch.object(
                        merge_section,
                        "_decode",
                        wraps=merge_section._decode,
                    ) as decode,
                    mock.patch.object(merge_section, "merge") as merge_call,
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: block exceeds the supported newline count limit\n",
                )
                self.assertNotIn(marker, stderr.getvalue())
                decode.assert_called_once_with(original, "target")
                merge_call.assert_not_called()
                temporary_open.assert_not_called()
                self.assertEqual(block.read_bytes(), block_bytes)

    def test_output_newline_limit_rejects_growth_without_writing(self):
        cases = (
            (
                "append",
                b"# Doc\n",
                b"## Notes\n\nnew.\n",
            ),
            (
                "replace",
                b"# Doc\n\n## Notes\nold.\n",
                b"## Notes\n\nnew.\nextra.\n",
            ),
        )

        for case_name, original, block_bytes in cases:
            target = self._write("%s-target.md" % case_name, original)
            block = self._write("%s-section.md" % case_name, block_bytes)
            for check_args in ((), ("--check",)):
                with self.subTest(case=case_name, check=bool(check_args)):
                    stderr = io.StringIO()
                    stdout = io.StringIO()
                    with (
                        mock.patch.object(
                            merge_section,
                            "_MAX_TARGET_NEWLINES",
                            original.count(b"\n"),
                        ),
                        mock.patch.object(
                            merge_section,
                            "_MAX_BLOCK_NEWLINES",
                            block_bytes.count(b"\n"),
                        ),
                        mock.patch.object(
                            merge_section,
                            "_MAX_OUTPUT_NEWLINES",
                            original.count(b"\n"),
                        ),
                        mock.patch.object(
                            merge_section,
                            "_open_atomic_temporary",
                        ) as temporary_open,
                        mock.patch.object(sys, "stderr", stderr),
                        mock.patch.object(sys, "stdout", stdout),
                    ):
                        result = merge_section.main(
                            [str(target), str(block), *check_args]
                        )

                    self.assertEqual(result, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(
                        stderr.getvalue(),
                        "error: merged output exceeds the supported newline count limit\n",
                    )
                    temporary_open.assert_not_called()
                    self.assertEqual(target.read_bytes(), original)

    def test_exact_newline_limits_preserve_lf_crlf_bom_and_second_run(self):
        text = "# Doc\n\n## Notes\n\nold.\n"
        block_text = "## Notes\n\nnew.\n"
        expected_text = "# Doc\n\n## Notes\n\nnew.\n"

        for label, eol, bom in (
            ("lf", b"\n", b""),
            ("bom-crlf", b"\r\n", merge_section._BOM),
        ):
            target_bytes = bom + text.replace("\n", eol.decode()).encode("utf-8")
            block_bytes = block_text.replace("\n", eol.decode()).encode("utf-8")
            expected = bom + expected_text.replace("\n", eol.decode()).encode(
                "utf-8"
            )
            target = self._write("%s-target.md" % label, target_bytes)
            block = self._write("%s-section.md" % label, block_bytes)

            with self.subTest(style=label):
                with (
                    mock.patch.object(
                        merge_section,
                        "_MAX_TARGET_NEWLINES",
                        target_bytes.count(b"\n"),
                    ),
                    mock.patch.object(
                        merge_section,
                        "_MAX_BLOCK_NEWLINES",
                        block_bytes.count(b"\n"),
                    ),
                    mock.patch.object(
                        merge_section,
                        "_MAX_OUTPUT_NEWLINES",
                        expected.count(b"\n"),
                    ),
                ):
                    changed, action = merge_section.merge_file(target, block)
                    self.assertTrue(changed)
                    self.assertEqual(action, "replaced")
                    self.assertEqual(target.read_bytes(), expected)

                    changed, action = merge_section.merge_file(target, block)
                    self.assertFalse(changed)
                    self.assertEqual(action, "unchanged")
                    self.assertEqual(target.read_bytes(), expected)

    def test_commit_precheck_tolerates_path_timestamp_precision_difference(self):
        original = b"# Doc\n\n## Notes\n\nold.\n"
        target = self._write("target.md", original)
        target_stat, observed = merge_section._read_regular_file_snapshot(
            target,
            max_bytes=merge_section._MAX_TARGET_BYTES,
            oversize_error=merge_section._TARGET_OVERSIZE_ERROR,
        )
        original_inspect = merge_section._inspect_target
        inspect_calls = 0
        drifted_path_stat = None

        class PathTimestampPrecisionView:
            """Expose one path-only timestamp delta while preserving identity."""

            def __init__(self, source):
                self._source = source

            def __getattr__(self, name):
                if name == "st_mtime_ns":
                    return self._source.st_mtime_ns + 1
                return getattr(self._source, name)

        def inspect_with_path_precision_drift(path, **kwargs):
            nonlocal inspect_calls, drifted_path_stat
            inspect_calls += 1
            current = original_inspect(path, **kwargs)
            if inspect_calls == 1:
                drifted_path_stat = PathTimestampPrecisionView(current)
                return drifted_path_stat
            return current

        # Windowsではpath lstatとhandle fstatのtimestamp精度が異なり得る。
        # preliminary guardはidentityだけを見て、最終descriptor fingerprintは
        # 従来どおりstrictに比較する二段階契約を固定する。
        with mock.patch.object(
            merge_section,
            "_inspect_target",
            side_effect=inspect_with_path_precision_drift,
        ):
            merge_section._assert_target_unchanged(
                target,
                target_stat,
                observed,
            )

        self.assertGreaterEqual(inspect_calls, 3)
        self.assertTrue(
            merge_section._same_file_identity(drifted_path_stat, target_stat)
        )
        self.assertNotEqual(
            merge_section._stat_fingerprint(drifted_path_stat),
            merge_section._stat_fingerprint(target_stat),
        )
        self.assertEqual(target.read_bytes(), original)

    def test_replacement_snapshot_uses_exact_expected_byte_bound(self):
        target = self.dir / "target.md"
        replacement = b"# Doc\n\n## Notes\n\nnew.\n"
        descriptor, temporary = merge_section._open_atomic_temporary(target)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
            expected_stat = os.fstat(stream.fileno())
        original_snapshot = merge_section._read_regular_file_snapshot

        with mock.patch.object(
            merge_section,
            "_read_regular_file_snapshot",
            wraps=original_snapshot,
        ) as snapshot:
            current = merge_section._verified_replacement_snapshot(
                temporary,
                expected_stat,
                replacement,
            )

        self.assertTrue(
            merge_section._same_file_identity(current, expected_stat)
        )
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(snapshot.call_args.kwargs["max_bytes"], len(replacement))
        self.assertEqual(
            snapshot.call_args.kwargs["oversize_error"],
            "replacement temporary changed before commit",
        )

        # expected lengthを1 byte超えた同一objectは、全量を読む前に固定診断へ
        # fail closedし、temporaryのbytes自体は変更しない。
        with temporary.open("ab") as stream:
            stream.write(b"x")
        oversized = replacement + b"x"
        with self.assertRaisesRegex(
            merge_section.MergeError,
            "^replacement temporary changed before commit$",
        ):
            merge_section._verified_replacement_snapshot(
                temporary,
                expected_stat,
                replacement,
            )
        self.assertEqual(temporary.read_bytes(), oversized)

    def test_cli_rejects_oversized_target_without_writing_or_path_reflection(self):
        marker = "非公開ターゲット標識"
        original = ("# Doc\n\n## Notes\n\n%s\n" % marker).encode("utf-8")
        block_bytes = b"## Notes\n\nnew.\n"
        target = self._write("%s.md" % marker, original)
        block = self._write("section.md", block_bytes)

        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        merge_section,
                        "_MAX_TARGET_BYTES",
                        len(original) - 1,
                    ),
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: target exceeds the supported byte limit\n",
                )
                self.assertNotIn(marker, stderr.getvalue())
                self.assertNotIn(block.name, stderr.getvalue())
                temporary_open.assert_not_called()
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)
                self.assertEqual(
                    sorted(path.name for path in self.dir.iterdir()),
                    sorted((block.name, target.name)),
                )

    def test_cli_rejects_oversized_block_without_writing_or_path_reflection(self):
        marker = "非公開ブロック標識"
        original = b"# Doc\n\n## Notes\n\nold.\n"
        block_bytes = ("## Notes\n\n%s\n" % marker).encode("utf-8")
        target = self._write("target.md", original)
        block = self._write("%s.md" % marker, block_bytes)

        for check_args in ((), ("--check",)):
            with self.subTest(check=bool(check_args)):
                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        merge_section,
                        "_MAX_BLOCK_BYTES",
                        len(block_bytes) - 1,
                    ),
                    mock.patch.object(
                        merge_section,
                        "_open_atomic_temporary",
                    ) as temporary_open,
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(sys, "stdout", stdout),
                ):
                    result = merge_section.main(
                        [str(target), str(block), *check_args]
                    )

                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "error: block exceeds the supported byte limit\n",
                )
                self.assertNotIn(marker, stderr.getvalue())
                self.assertNotIn(target.name, stderr.getvalue())
                temporary_open.assert_not_called()
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)
                self.assertEqual(
                    sorted(path.name for path in self.dir.iterdir()),
                    sorted((block.name, target.name)),
                )

    def test_commit_growth_is_metadata_change_and_cleans_temporary(self):
        original = b"# Doc\n\n## Notes\n\nold.\n"
        target = self._write("target.md", original)
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_open = merge_section._open_atomic_temporary

        def open_then_grow_target(target_path):
            result = original_open(target_path)
            # 初回snapshot後にexact-limitからlimit+1へ外部成長させる。
            # byte超過よりbaseline metadata driftを優先し、外部bytesを守る。
            target.write_bytes(original + b"x")
            return result

        with (
            mock.patch.object(
                merge_section,
                "_MAX_TARGET_BYTES",
                len(original),
            ),
            mock.patch.object(
                merge_section,
                "_open_atomic_temporary",
                side_effect=open_then_grow_target,
            ),
        ):
            with self.assertRaisesRegex(
                merge_section.MergeError,
                "^target metadata changed during merge$",
            ):
                merge_section.merge_file(target, block)

        self.assertEqual(target.read_bytes(), original + b"x")
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["section.md", "target.md"],
        )

    def test_crlf_and_bom_are_preserved_and_stable(self):
        fixture = "replace-existing-section"
        bom = b"\xef\xbb\xbf"
        crlf_input = bom + load(fixture, "input.md").replace("\n", "\r\n").encode(
            "utf-8"
        )
        target = self._write("target.md", crlf_input)
        block = self._write(
            "section.md", load(fixture, "section.md").encode("utf-8")
        )

        changed, action = merge_section.merge_file(target, block)
        self.assertTrue(changed)
        self.assertEqual(action, "replaced")
        expected = bom + load(fixture, "expected.md").replace("\n", "\r\n").encode(
            "utf-8"
        )
        self.assertEqual(target.read_bytes(), expected)

        changed, action = merge_section.merge_file(target, block)
        self.assertFalse(changed)
        self.assertEqual(action, "unchanged")

    def test_frontmatter_merge_preserves_crlf_and_bom(self):
        bom = b"\xef\xbb\xbf"
        source = (
            "---\n"
            "title: Synthetic guide\n"
            "## Managed\n"
            "owner: example\n"
            "---\n"
            "\n"
            "# Document\n"
        )
        target = self._write(
            "target.md", bom + source.replace("\n", "\r\n").encode("utf-8")
        )
        block = self._write(
            "section.md", b"## Managed\n\nCanonical body.\n"
        )

        changed, action = merge_section.merge_file(target, block)
        self.assertTrue(changed)
        self.assertEqual(action, "appended")
        merged_bytes = target.read_bytes()
        self.assertTrue(merged_bytes.startswith(bom))
        self.assertNotIn(b"\n", merged_bytes.replace(b"\r\n", b""))
        self.assertIn(b"owner: example\r\n---", merged_bytes)

        changed, action = merge_section.merge_file(target, block)
        self.assertFalse(changed)
        self.assertEqual(action, "unchanged")

    def test_html_block_merge_preserves_crlf_and_bom(self):
        fixture = "html-block-heading-literal"
        bom = b"\xef\xbb\xbf"
        target = self._write(
            "target.md",
            bom
            + load(fixture, "input.md").replace("\n", "\r\n").encode("utf-8"),
        )
        block = self._write(
            "section.md", load(fixture, "section.md").encode("utf-8")
        )

        # HTML-aware range selection must not weaken byte-level encoding and
        # EOL preservation at the file boundary.
        changed, action = merge_section.merge_file(target, block)
        self.assertTrue(changed)
        self.assertEqual(action, "replaced")
        expected = (
            bom
            + load(fixture, "expected.md").replace("\n", "\r\n").encode("utf-8")
        )
        self.assertEqual(target.read_bytes(), expected)

        changed, action = merge_section.merge_file(target, block)
        self.assertFalse(changed)
        self.assertEqual(action, "unchanged")

    def test_missing_target_is_created_by_append(self):
        fixture = "append-missing-section"
        target = self.dir / "new.md"
        block = self._write(
            "section.md", load(fixture, "section.md").encode("utf-8")
        )
        changed, action = merge_section.merge_file(target, block)
        self.assertTrue(changed)
        self.assertEqual(action, "appended")
        self.assertEqual(
            target.read_bytes().decode("utf-8"), load(fixture, "section.md")
        )
        if os.name == "nt":
            self._assert_windows_owner_only_dacl(target)
        else:
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX hard-link commit is POSIX-only")
    def test_missing_target_cleanup_failure_reports_committed_partial_state(self):
        target = self.dir / "new.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")

        with mock.patch.object(
            merge_section,
            "_unlink_owned_path",
            side_effect=OSError("synthetic persistent unlink failure"),
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(len(caught.exception.artifacts), 1)
        temporary = caught.exception.artifacts[0]
        self.assertEqual(target.read_bytes(), b"## Notes\n\nnew.\n")
        self.assertEqual(temporary.read_bytes(), target.read_bytes())
        self.assertTrue(
            merge_section._same_file_identity(
                target.lstat(),
                temporary.lstat(),
            )
        )
        self.assertEqual(target.lstat().st_nlink, 2)

        # テスト自身が意図的に残した hard link は、元 helper で回収して
        # 後続ケースへ residue を持ち越さない。
        merge_section._unlink_owned_path(temporary, temporary.lstat())

    @unittest.skipIf(os.name == "nt", "POSIX hard-link commit is POSIX-only")
    def test_missing_target_link_interrupt_reports_committed_state(self):
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_link = os.link

        def link_then_interrupt(source, destination, **kwargs):
            original_link(source, destination, **kwargs)
            raise KeyboardInterrupt("synthetic post-link interrupt")

        with mock.patch.object(
            os,
            "link",
            side_effect=link_then_interrupt,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, ())
        self.assertIn(b"new.", target.read_bytes())
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["section.md", "target.md"],
        )
        self.assertEqual(target.lstat().st_nlink, 1)

    def _run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "merge_section.py"), *args],
            capture_output=True,
            text=True,
        )

    def test_cli_merge_and_check_exit_codes(self):
        fixture = "replace-existing-section"
        target = self._write(
            "target.md", load(fixture, "input.md").encode("utf-8")
        )
        block = self._write(
            "section.md", load(fixture, "section.md").encode("utf-8")
        )

        stale = self._run_cli(str(target), str(block), "--check")
        self.assertEqual(stale.returncode, 1)

        merged = self._run_cli(str(target), str(block))
        self.assertEqual(merged.returncode, 0)
        self.assertEqual(
            target.read_bytes().decode("utf-8"), load(fixture, "expected.md")
        )

        canonical = self._run_cli(str(target), str(block), "--check")
        self.assertEqual(canonical.returncode, 0)

    def test_cli_rejects_invalid_block_with_exit_2(self):
        target = self._write("target.md", b"# Doc\n")
        block = self._write("section.md", b"## One\n\n## Two\n")
        result = self._run_cli(str(target), str(block))
        self.assertEqual(result.returncode, 2)
        self.assertIn("exactly one", result.stderr)

    def test_cli_rejects_utf8_setext_block_without_writing(self):
        original = (
            b"\xef\xbb\xbf# Synthetic document\r\n\r\n"
            b"Existing bytes must stay unchanged.\r\n"
        )
        block_bytes = (
            "## 管理節\r\n\r\n"
            "合成の子見出し\r\n"
            "---\t\r\n\r\n"
            "書き込まれてはいけない本文。\r\n"
        ).encode("utf-8")
        target = self._write("target.md", original)
        block = self._write("section.md", block_bytes)

        # 通常実行とdry-runを同じwrite前validationへ通し、BOM/CRLFを含む
        # targetとUTF-8 blockの双方がbyte単位で不変であることを固定する。
        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                target.write_bytes(original)
                block.write_bytes(block_bytes)
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn("possible setext heading", result.stderr)
                self.assertNotIn("合成の子見出し", result.stderr)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)

    def test_cli_rejects_unclosed_frontmatter_without_writing(self):
        original = b"---\ntitle: Synthetic guide\n## Managed\nowner: example\n"
        target = self._write("target.md", original)
        block = self._write(
            "section.md", b"## Managed\n\nCanonical body.\n"
        )

        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn("unclosed YAML frontmatter", result.stderr)
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_unclosed_html_without_writing(self):
        original = (
            b"## Managed\n\nOld body.\n\n"
            b"<!--\n"
            b"## Hidden section\n"
        )
        target = self._write("target.md", original)
        block = self._write(
            "section.md", b"## Managed\n\nCanonical body.\n"
        )

        # 通常実行と dry-run の双方で同じ validation error にし、atomic write
        # へ到達しないことを元 bytes の完全一致で確認する。
        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "unclosed raw HTML block type 2",
                    result.stderr,
                )
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_indented_managed_h2_without_writing(self):
        original = (
            b"- Synthetic item\n"
            b"  ## Managed\n\n"
            b"Nested-looking body.\n"
        )
        target = self._write("target.md", original)
        block = self._write(
            "section.md", b"## Managed\n\nCanonical body.\n"
        )

        # 通常実行とdry-runを同じvalidation境界へ通し、部分的なcontainer
        # 解釈や自動reindentを行わず、元bytesを完全に維持する。
        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                target.write_bytes(original)
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "indented managed H2 outside literal regions",
                    result.stderr,
                )
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_closing_hash_alias_without_writing(self):
        original = (
            b"\xef\xbb\xbf# Synthetic document\r\n\r\n"
            b"  ## Managed ###\t\r\n\r\n"
            b"Old semantic duplicate.\r\n"
        )
        target = self._write("target.md", original)
        block = self._write(
            "section.md", b"## Managed\n\nCanonical body.\n"
        )

        # 通常実行とdry-runの両方を同じidentity validationへ通す。
        # CRLF/BOMを含む元bytesが一切変化しないことまで固定する。
        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                target.write_bytes(original)
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "closing-hash managed H2 alias outside literal regions",
                    result.stderr,
                )
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_closing_hash_block_without_writing(self):
        original = b"# Synthetic document\n\nExisting body.\n"
        target = self._write("target.md", original)
        block = self._write(
            "section.md", b"## Managed ##\n\nCanonical body.\n"
        )

        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "block heading must use plain form without a closing hash "
                    "sequence",
                    result.stderr,
                )
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_separator_alias_without_writing_bom_crlf(self):
        marker = "合成管理節"
        original = (
            b"\xef\xbb\xbf# Synthetic document\r\n\r\n"
            + ("  ##\t%s ###\t\r\n\r\n" % marker).encode("utf-8")
            + b"Old semantic duplicate.\r\n"
        )
        target = self._write("target.md", original)
        block = self._write(
            "section.md",
            ("## %s\n\nCanonical body.\n" % marker).encode("utf-8"),
        )

        # 通常実行とdry-runを同じidentity境界へ通し、拒否時のBOM/CRLFを維持する。
        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                target.write_bytes(original)
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "noncanonical managed H2 separator alias outside literal "
                    "regions",
                    result.stderr,
                )
                self.assertNotIn(marker, result.stderr)
                self.assertEqual(target.read_bytes(), original)

    def test_cli_rejects_noncanonical_block_separator_without_writing(self):
        marker = "合成管理節"
        original = b"\xef\xbb\xbf# Synthetic document\r\n\r\nExisting body.\r\n"
        block_bytes = ("##\t%s\r\n\r\nCanonical body.\r\n" % marker).encode(
            "utf-8"
        )
        target = self._write("target.md", original)
        block = self._write("section.md", block_bytes)

        # block側も初回append前の固定診断へ止め、両入力のbytesを変えない。
        for check_arg in ((), ("--check",)):
            with self.subTest(check=bool(check_arg)):
                target.write_bytes(original)
                block.write_bytes(block_bytes)
                result = self._run_cli(str(target), str(block), *check_arg)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "block heading must use exactly one ASCII space before "
                    "nonempty content",
                    result.stderr,
                )
                self.assertNotIn(marker, result.stderr)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(block.read_bytes(), block_bytes)

    def test_cli_preserves_non_ascii_heading_suffixes_with_bom_crlf(self):
        suffix_kinds = {
            "plain": "## Managed%s",
            "after-hashes": "## Managed ##%s",
        }
        for label, whitespace in (
            AsciiWhitespaceGrammarTests.non_ascii_whitespace.items()
        ):
            for suffix_kind, heading_template in suffix_kinds.items():
                with self.subTest(label=label, suffix=suffix_kind):
                    original_text = (
                        "# Synthetic document\r\n\r\n"
                        + (heading_template % whitespace)
                        + "\r\n\r\nDifferent section body.\r\n"
                    )
                    original = b"\xef\xbb\xbf" + original_text.encode("utf-8")
                    target = self._write(
                        "target-%s-%s.md" % (label, suffix_kind),
                        original,
                    )
                    block = self._write(
                        "section-%s-%s.md" % (label, suffix_kind),
                        b"## Managed\n\nCanonical body.\n",
                    )

                    # dry-runは「追記が必要」の1を返すだけで、元bytesへ触れない。
                    stale = self._run_cli(str(target), str(block), "--check")
                    self.assertEqual(stale.returncode, 1)
                    self.assertEqual(target.read_bytes(), original)

                    # 通常実行は別headingを保持して正本を追記し、BOM/CRLFを維持する。
                    applied = self._run_cli(str(target), str(block))
                    self.assertEqual(applied.returncode, 0)
                    updated = target.read_bytes()
                    self.assertTrue(updated.startswith(original))
                    self.assertIn(b"Different section body.\r\n", updated)
                    self.assertTrue(
                        updated.endswith(
                            b"\r\n## Managed\r\n\r\nCanonical body.\r\n"
                        )
                    )
                    self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))

                    canonical = self._run_cli(
                        str(target),
                        str(block),
                        "--check",
                    )
                    self.assertEqual(canonical.returncode, 0)

    def test_cli_non_ascii_fence_suffix_fails_closed_with_bom_crlf(self):
        for label, whitespace in (
            AsciiWhitespaceGrammarTests.non_ascii_whitespace.items()
        ):
            with self.subTest(label=label):
                original_text = (
                    "# Synthetic document\r\n\r\n"
                    "## Managed\r\n\r\nOld body.\r\n\r\n"
                    "```text\r\n"
                    "literal\r\n"
                    "```%s\r\n"
                    "# Protected inside the unclosed fence\r\n" % whitespace
                )
                original = b"\xef\xbb\xbf" + original_text.encode("utf-8")
                target = self._write("fence-target-%s.md" % label, original)
                block = self._write(
                    "fence-section-%s.md" % label,
                    b"## Managed\n\nCanonical body.\n",
                )

                for check_arg in ((), ("--check",)):
                    with self.subTest(check=bool(check_arg)):
                        result = self._run_cli(
                            str(target),
                            str(block),
                            *check_arg,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(
                            "target ends inside an unclosed code fence",
                            result.stderr,
                        )
                        self.assertEqual(target.read_bytes(), original)

    def test_cli_non_ascii_setext_content_fails_closed_with_bom_crlf(self):
        for label, whitespace in (
            AsciiWhitespaceGrammarTests.non_ascii_whitespace.items()
        ):
            with self.subTest(label=label):
                original_text = (
                    "# Synthetic document\r\n\r\n"
                    "## Managed\r\n\r\nOld body.\r\n\r\n"
                    "%s\r\n---\r\n\r\nProtected tail.\r\n\r\n"
                    "## Next\r\n\r\nKeep.\r\n" % whitespace
                )
                original = b"\xef\xbb\xbf" + original_text.encode("utf-8")
                target = self._write("setext-target-%s.md" % label, original)
                block = self._write(
                    "setext-section-%s.md" % label,
                    b"## Managed\n\nCanonical body.\n",
                )

                for check_arg in ((), ("--check",)):
                    with self.subTest(check=bool(check_arg)):
                        result = self._run_cli(
                            str(target),
                            str(block),
                            *check_arg,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(
                            "possible setext heading",
                            result.stderr,
                        )
                        self.assertEqual(target.read_bytes(), original)

    def test_api_and_cli_reject_multiline_reference_html_without_writing(self):
        originals = {
            "split-label": (
                b"[\n"
                b"foo\n"
                b"]: /synthetic\n"
                b"===\n"
                b"<Synthetic-Widget>\n"
                b"## Managed\n"
                b"</Synthetic-Widget>\n"
            ),
            "escaped-line-end": (
                b"[foo\\\n"
                b"bar]: /synthetic\n"
                b"===\n"
                b"<Synthetic-Widget>\n"
                b"## Managed\n"
                b"</Synthetic-Widget>\n"
            ),
        }
        block = self._write(
            "section.md", b"## Managed\n\nCanonical body.\n"
        )

        # API と CLI の全mutation経路で、曖昧な複数行definitionを同じ
        # validation error に畳み込み、元bytesを1回も変更しない。
        for label, original in originals.items():
            with self.subTest(label=label):
                target = self._write("%s.md" % label, original)
                with self.assertRaisesRegex(
                    merge_section.MergeError,
                    "ambiguous setext context after a possible link reference "
                    "definition",
                ):
                    merge_section.merge_file(target, block)
                self.assertEqual(target.read_bytes(), original)

                for check_arg in ((), ("--check",)):
                    with self.subTest(check=bool(check_arg)):
                        result = self._run_cli(
                            str(target), str(block), *check_arg
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(
                            "ambiguous setext context after a possible link "
                            "reference definition",
                            result.stderr,
                        )
                        self.assertEqual(target.read_bytes(), original)

    def test_cli_reports_tri_state_and_every_recovery_artifact(self):
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        artifacts = (
            self.dir / ".target.md.private.tmp",
            self.dir / ".target.md.recovery-backup",
        )
        failure = merge_section.AtomicCommitError(
            "synthetic ambiguous commit",
            committed=None,
            recovered=False,
            artifacts=artifacts,
        )
        stderr = io.StringIO()

        with (
            mock.patch.object(
                merge_section,
                "merge_file",
                side_effect=failure,
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            result = merge_section.main([str(target), str(block)])

        self.assertEqual(result, 2)
        output = stderr.getvalue()
        self.assertIn(
            "commit-state: committed=unknown recovered=false",
            output,
        )
        for artifact in artifacts:
            self.assertIn(
                "recovery-artifact: %s" % ascii(os.fspath(artifact)),
                output,
            )

    def test_cr_only_line_endings_are_rejected(self):
        target = self._write("target.md", b"# T\r\r## Notes\rold.\r")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        result = self._run_cli(str(target), str(block))
        self.assertEqual(result.returncode, 2)
        self.assertIn("CR line endings", result.stderr)

    def test_mixed_eol_normalization_is_reported_not_hidden(self):
        # Content is already canonical; only the mixed line endings change.
        # The CLI must say so ("normalized"), not claim "unchanged".
        target = self._write("target.md", b"# T\r\n\n## Notes\nnew.\n")
        block = self._write("section.md", b"## Notes\nnew.\n")

        check = self._run_cli(str(target), str(block), "--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn("would-normalize", check.stdout)

        first = self._run_cli(str(target), str(block))
        self.assertEqual(first.returncode, 0)
        self.assertIn("normalized", first.stdout)
        self.assertEqual(target.read_bytes(), b"# T\r\n\r\n## Notes\r\nnew.\r\n")

        second = self._run_cli(str(target), str(block))
        self.assertEqual(second.returncode, 0)
        self.assertIn("unchanged", second.stdout)

    def test_replace_failure_preserves_original_and_removes_temporary_file(self):
        # The final rename is the only operation allowed to replace TARGET.
        # A write/rename failure must leave both the original bytes and the
        # directory contents unchanged.
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_names = {path.name for path in self.dir.iterdir()}

        if os.name == "nt":
            # ReplaceFileW error 1176 with a supplied backup retains both
            # original names. Exercise the raw seam so the commit helper owns
            # and safely removes its temp/placeholder artifacts.
            failure = mock.patch.object(
                merge_section,
                "_replace_file_windows_raw",
                return_value=1176,
            )
        else:
            # Patch the platform primitive, not the ownership-taking helper:
            # the helper must observe the failure and remove its private name.
            failure = mock.patch.object(
                os,
                "replace",
                side_effect=OSError("synthetic replace failure"),
            )
        with failure:
            with self.assertRaises(OSError):
                merge_section.merge_file(target, block)

        self.assertEqual(target.read_bytes(), b"# Doc\n\n## Notes\n\nold.\n")
        self.assertEqual(
            {path.name for path in self.dir.iterdir()},
            original_names,
        )

    def test_target_change_before_final_recheck_is_detected(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_open = merge_section._open_atomic_temporary

        def open_then_mutate(target_path):
            result = original_open(target_path)
            target.write_bytes(b"# Doc\n\n## Notes\n\nexternal.\n")
            return result

        with mock.patch.object(
            merge_section,
            "_open_atomic_temporary",
            side_effect=open_then_mutate,
        ):
            with self.assertRaisesRegex(merge_section.MergeError, "changed"):
                merge_section.merge_file(target, block)

        self.assertEqual(
            target.read_bytes(),
            b"# Doc\n\n## Notes\n\nexternal.\n",
        )
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["section.md", "target.md"],
        )

    def test_concurrently_created_target_is_not_overwritten(self):
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_open = merge_section._open_atomic_temporary

        def open_then_create(target_path):
            result = original_open(target_path)
            target.write_bytes(b"# External\n")
            return result

        with mock.patch.object(
            merge_section,
            "_open_atomic_temporary",
            side_effect=open_then_create,
        ):
            with self.assertRaisesRegex(merge_section.MergeError, "appeared"):
                merge_section.merge_file(target, block)

        self.assertEqual(target.read_bytes(), b"# External\n")
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["section.md", "target.md"],
        )

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not portable")
    def test_atomic_replace_preserves_existing_permission_bits(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        target.chmod(0o640)

        merge_section.merge_file(target, block)

        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    @unittest.skipUnless(os.name == "nt", "NTFS named streams are Windows-only")
    def test_atomic_replace_preserves_windows_named_stream(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        stream = Path(str(target) + ":merge-metadata")
        try:
            stream.write_bytes(b"preserved")
        except OSError:
            self.skipTest("named streams are unavailable on this filesystem")

        merge_section.merge_file(target, block)

        self.assertEqual(stream.read_bytes(), b"preserved")

    @unittest.skipUnless(os.name == "nt", "Windows metadata is Windows-only")
    def test_atomic_replace_preserves_windows_dacl_and_hidden_attribute(self):
        import ctypes
        from ctypes import wintypes

        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        self._add_windows_everyone_read_ace(target)

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_attributes = kernel32.GetFileAttributesW
        get_attributes.argtypes = (wintypes.LPCWSTR,)
        get_attributes.restype = wintypes.DWORD
        set_attributes = kernel32.SetFileAttributesW
        set_attributes.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
        set_attributes.restype = wintypes.BOOL
        invalid_attributes = 0xFFFFFFFF
        hidden = 0x00000002
        before_attributes = get_attributes(str(target))
        self.assertNotEqual(before_attributes, invalid_attributes)
        self.assertTrue(
            set_attributes(str(target), before_attributes | hidden)
        )
        expected_dacl = self._windows_dacl_sddl(target)

        merge_section.merge_file(target, block)

        actual_dacl = self._windows_dacl_sddl(target)
        expected_aces = expected_dacl[expected_dacl.index("(") :]
        actual_aces = actual_dacl[actual_dacl.index("(") :]
        # ReplaceFileW may add the AI bookkeeping control flag while keeping
        # the protected policy and every semantic ACE unchanged.
        self.assertIn("P", expected_dacl[: expected_dacl.index("(")])
        self.assertIn("P", actual_dacl[: actual_dacl.index("(")])
        self.assertEqual(actual_aces, expected_aces)
        after_attributes = get_attributes(str(target))
        self.assertNotEqual(after_attributes, invalid_attributes)
        self.assertTrue(after_attributes & hidden)
        self.assertIn(b"new.", target.read_bytes())

    @unittest.skipIf(os.name == "nt", "POSIX extended attributes are unavailable")
    def test_atomic_replace_preserves_bounded_extended_attributes(self):
        if not all(
            hasattr(os, name)
            for name in ("setxattr", "getxattr", "listxattr")
        ):
            self.skipTest("extended attributes are unavailable on this host")
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        attribute_name = "user.markdown-section-merge-test"
        try:
            os.setxattr(target, attribute_name, b"preserved")
        except OSError:
            self.skipTest("user extended attributes are unavailable")

        merge_section.merge_file(target, block)

        self.assertEqual(
            os.getxattr(target, attribute_name),
            b"preserved",
        )

    def test_symbolic_link_target_is_rejected_without_touching_referent(self):
        referent = self._write("referent.md", b"# Doc\n\n## Notes\n\nold.\n")
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        try:
            target.symlink_to(referent)
        except (NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable on this host")

        # referentは0-byte上限を超えるが、no-follow/type診断をsizeより優先する。
        with mock.patch.object(merge_section, "_MAX_TARGET_BYTES", 0):
            with self.assertRaisesRegex(
                merge_section.MergeError,
                "symbolic link|reparse point",
            ):
                merge_section.merge_file(target, block)

        self.assertTrue(target.is_symlink())
        self.assertEqual(
            referent.read_bytes(),
            b"# Doc\n\n## Notes\n\nold.\n",
        )

    def test_symbolic_link_block_is_rejected_before_byte_limit_read(self):
        target = self._write("target.md", b"# Doc\n")
        referent = self._write("referent.md", b"## Notes\n\nnew.\n")
        block = self.dir / "section.md"
        try:
            block.symlink_to(referent)
        except (NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable on this host")

        with mock.patch.object(merge_section, "_MAX_BLOCK_BYTES", 0):
            with self.assertRaisesRegex(
                merge_section.MergeError,
                "symbolic link|reparse point",
            ):
                merge_section.merge_file(target, block)

        self.assertEqual(target.read_bytes(), b"# Doc\n")
        self.assertTrue(block.is_symlink())
        self.assertEqual(referent.read_bytes(), b"## Notes\n\nnew.\n")

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_fifo_target_is_rejected_before_read(self):
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        os.mkfifo(target)

        with self.assertRaisesRegex(merge_section.MergeError, "regular file"):
            merge_section.merge_file(target, block)

        self.assertTrue(stat.S_ISFIFO(target.lstat().st_mode))

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_fifo_replacement_is_rejected_during_final_recheck(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_open = merge_section._open_atomic_temporary

        def open_then_replace_with_fifo(target_path):
            result = original_open(target_path)
            target.unlink()
            os.mkfifo(target)
            return result

        with mock.patch.object(
            merge_section,
            "_open_atomic_temporary",
            side_effect=open_then_replace_with_fifo,
        ):
            with self.assertRaisesRegex(merge_section.MergeError, "regular file"):
                merge_section.merge_file(target, block)

        self.assertTrue(stat.S_ISFIFO(target.lstat().st_mode))
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["section.md", "target.md"],
        )

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_fifo_block_is_rejected_before_read(self):
        target = self._write("target.md", b"# Doc\n")
        block = self.dir / "section.md"
        os.mkfifo(block)

        with self.assertRaisesRegex(merge_section.MergeError, "regular file"):
            merge_section.merge_file(target, block)

        self.assertEqual(target.read_bytes(), b"# Doc\n")
        self.assertTrue(stat.S_ISFIFO(block.lstat().st_mode))

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_fifo_temp_swap_is_rejected_before_missing_target_commit(self):
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_commit = merge_section._commit_temporary
        captured = {}

        def swap_then_commit(
            target_path,
            temporary,
            target_stat,
            original_bytes,
            replacement_stat,
            replacement_bytes,
        ):
            temporary.unlink()
            os.mkfifo(temporary)
            captured["temporary"] = temporary
            return original_commit(
                target_path,
                temporary,
                target_stat,
                original_bytes,
                replacement_stat,
                replacement_bytes,
            )

        with mock.patch.object(
            merge_section,
            "_commit_temporary",
            side_effect=swap_then_commit,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertFalse(target.exists())
        self.assertTrue(
            stat.S_ISFIFO(captured["temporary"].lstat().st_mode)
        )

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_fifo_temp_swap_is_rejected_before_existing_target_replace(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_commit = merge_section._commit_temporary
        captured = {}

        def swap_then_commit(
            target_path,
            temporary,
            target_stat,
            original_bytes,
            replacement_stat,
            replacement_bytes,
        ):
            temporary.unlink()
            os.mkfifo(temporary)
            captured["temporary"] = temporary
            return original_commit(
                target_path,
                temporary,
                target_stat,
                original_bytes,
                replacement_stat,
                replacement_bytes,
            )

        with mock.patch.object(
            merge_section,
            "_commit_temporary",
            side_effect=swap_then_commit,
        ):
            with self.assertRaises(merge_section.AtomicCommitError) as caught:
                merge_section.merge_file(target, block)

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(
            target.read_bytes(),
            b"# Doc\n\n## Notes\n\nold.\n",
        )
        self.assertTrue(
            stat.S_ISFIFO(captured["temporary"].lstat().st_mode)
        )

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_fifo_swap_after_open_is_rejected_before_initial_read(self):
        target = self._write("target.md", b"# Doc\n\n## Notes\n\nold.\n")
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        original_open = merge_section._open_regular_read_descriptor

        def open_then_replace_with_fifo(
            target_path,
            missing_ok=False,
            reject_encrypted=True,
        ):
            descriptor = original_open(
                target_path,
                missing_ok=missing_ok,
                reject_encrypted=reject_encrypted,
            )
            target.unlink()
            os.mkfifo(target)
            return descriptor

        with mock.patch.object(
            merge_section,
            "_open_regular_read_descriptor",
            side_effect=open_then_replace_with_fifo,
        ):
            with self.assertRaisesRegex(merge_section.MergeError, "regular file"):
                merge_section.merge_file(target, block)

        self.assertTrue(stat.S_ISFIFO(target.lstat().st_mode))
        self.assertEqual(
            sorted(path.name for path in self.dir.iterdir()),
            ["section.md", "target.md"],
        )

    def test_hard_link_target_is_rejected_without_touching_peer(self):
        peer = self._write("peer.md", b"# Doc\n\n## Notes\n\nold.\n")
        target = self.dir / "target.md"
        block = self._write("section.md", b"## Notes\n\nnew.\n")
        try:
            os.link(peer, target)
        except (NotImplementedError, OSError):
            self.skipTest("hard links are unavailable on this host")

        with self.assertRaisesRegex(merge_section.MergeError, "hard link"):
            merge_section.merge_file(target, block)

        self.assertEqual(peer.read_bytes(), b"# Doc\n\n## Notes\n\nold.\n")


class PeakMemoryMeasurementTests(unittest.TestCase):
    """縮小fixtureで計測CLIのprocess・schema契約だけを固定する。"""

    def test_default_measurement_matrix_stays_within_target_line_budget(self):
        # 8 MiBの明示上限は維持しつつ、既定matrixは高密度LFでも行数上限内に収める。
        self.assertEqual(measure_peak_memory.MAX_TARGET_BYTES, 8 * 1024 * 1024)
        self.assertEqual(measure_peak_memory.DEFAULT_TARGET_BYTES, 1 * 1024 * 1024)

        parser = measure_peak_memory._build_parser()
        self.assertEqual(
            parser.parse_args([]).target_bytes,
            measure_peak_memory.DEFAULT_TARGET_BYTES,
        )
        self.assertEqual(
            parser.parse_args(
                ["--target-bytes", str(measure_peak_memory.MAX_TARGET_BYTES)]
            ).target_bytes,
            measure_peak_memory.MAX_TARGET_BYTES,
        )
        with io.StringIO() as stderr, mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(
                    ["--target-bytes", str(measure_peak_memory.MAX_TARGET_BYTES + 1)]
                )
        self.assertEqual(raised.exception.code, 2)

        for eol in (b"\n", b"\r\n"):
            with self.subTest(eol=eol):
                line_count, _last_text_bytes, _has_remainder = (
                    measure_peak_memory._short_line_shape(
                        measure_peak_memory.DEFAULT_TARGET_BYTES,
                        eol,
                    )
                )
                self.assertLessEqual(line_count, merge_section._MAX_TARGET_NEWLINES)

    def test_reduced_matrix_reports_actions_bytes_and_peak_metrics(self):
        # CIでは64 KiBへ縮小し、OS依存のpeak値そのものは合否に使わない。
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "measure_peak_memory.py"),
                "--target-bytes",
                str(64 * 1024),
                "--repetitions",
                "1",
                "--timeout-seconds",
                "30",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(result.returncode, 0, "measurement CLI failed")
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["measurement"], "process-peak-rss")
        self.assertEqual(report["configuration"]["target_bytes"], 64 * 1024)
        self.assertEqual(report["configuration"]["repetitions"], 1)

        expected_actions = {
            "lf-short-lines-append": "appended",
            "lf-short-lines-replace": "replaced",
            "crlf-short-lines-append": "appended",
            "crlf-short-lines-replace": "replaced",
            "mixed-eol-normalize": "normalized",
        }
        cases = {case["case_id"]: case for case in report["cases"]}
        self.assertEqual(set(cases), set(expected_actions))

        for case_id, expected_action in expected_actions.items():
            with self.subTest(case_id=case_id):
                samples = cases[case_id]["samples"]
                self.assertEqual(len(samples), 1)
                sample = samples[0]
                self.assertEqual(sample["actual_action"], expected_action)
                self.assertEqual(sample["expected_action"], expected_action)
                self.assertTrue(sample["changed"])
                self.assertGreater(sample["target_line_count"], 0)
                self.assertGreater(sample["peak_bytes"], 0)
                self.assertIsNone(sample["current_bytes"])
                self.assertIsNone(sample["tracer_overhead_bytes"])
                self.assertLessEqual(sample["target_bytes_before"], 64 * 1024)
                self.assertLessEqual(sample["final_bytes"], 64 * 1024)
                self.assertEqual(sample["temporary_artifact_count"], 0)

        # LF append/replaceはproduction上限と同じexact-output closureを縮小再現する。
        for case_id in ("lf-short-lines-append", "lf-short-lines-replace"):
            self.assertEqual(
                cases[case_id]["samples"][0]["final_bytes"],
                64 * 1024,
            )

        # stdoutは再現可能なJSONだけとし、temporary pathを証跡へ混ぜない。
        self.assertNotIn("work_dir", result.stdout)
        self.assertNotIn(str(REPO_ROOT), result.stdout)

        # tracemallocは高オーバーヘッドの補助metricとして最小caseでも契約を固定する。
        traced = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "measure_peak_memory.py"),
                "--target-bytes",
                str(16 * 1024),
                "--repetitions",
                "1",
                "--timeout-seconds",
                "30",
                "--metric",
                "python-tracemalloc",
                "--case",
                "lf-short-lines-append",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(traced.returncode, 0, "tracemalloc worker failed")
        traced_report = json.loads(traced.stdout)
        self.assertEqual(traced_report["measurement"], "python-tracemalloc")
        traced_sample = traced_report["cases"][0]["samples"][0]
        self.assertGreater(traced_sample["peak_bytes"], 0)
        self.assertGreaterEqual(
            traced_sample["peak_bytes"],
            traced_sample["current_bytes"],
        )
        self.assertGreater(traced_sample["tracer_overhead_bytes"], 0)

        # key集合だけでなくaction等の意味契約も親processで再検証する。
        invalid_sample = dict(cases["lf-short-lines-append"]["samples"][0])
        invalid_sample["actual_action"] = "unchanged"
        with self.assertRaisesRegex(
            measure_peak_memory.MeasurementError,
            "action contract",
        ):
            measure_peak_memory._validate_worker_record(
                {
                    "schema_version": 1,
                    "ok": True,
                    "sample": invalid_sample,
                },
                "lf-short-lines-append",
                1,
                64 * 1024,
                measure_peak_memory.PROCESS_PEAK_METRIC,
            )

        for nonfinite in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(nonfinite=repr(nonfinite)):
                nonfinite_sample = dict(
                    cases["lf-short-lines-append"]["samples"][0]
                )
                nonfinite_sample["peak_to_input_ratio"] = nonfinite
                with self.assertRaisesRegex(
                    measure_peak_memory.MeasurementError,
                    "numeric contract",
                ):
                    measure_peak_memory._validate_worker_record(
                        {
                            "schema_version": 1,
                            "ok": True,
                            "sample": nonfinite_sample,
                        },
                        "lf-short-lines-append",
                        1,
                        64 * 1024,
                        measure_peak_memory.PROCESS_PEAK_METRIC,
                    )

        mismatched_ratio = dict(cases["lf-short-lines-append"]["samples"][0])
        mismatched_ratio["peak_to_input_ratio"] += 1
        with self.assertRaisesRegex(
            measure_peak_memory.MeasurementError,
            "ratio contract",
        ):
            measure_peak_memory._validate_worker_record(
                {"schema_version": 1, "ok": True, "sample": mismatched_ratio},
                "lf-short-lines-append",
                1,
                64 * 1024,
                measure_peak_memory.PROCESS_PEAK_METRIC,
            )

    def test_worker_output_is_stopped_at_limit_plus_one(self):
        # stdout/stderrのどちらでも、全量captureせず上限+1でchildをkillする。
        for stream_name in ("stdout", "stderr"):
            with self.subTest(stream=stream_name):
                noisy_child = (
                    "import sys,time; stream=sys.%s.buffer; "
                    "stream.write(b'x' * %d); stream.flush(); time.sleep(10)"
                    % (
                        stream_name,
                        measure_peak_memory.MAX_WORKER_OUTPUT_BYTES + 1,
                    )
                )
                with self.assertRaisesRegex(
                    measure_peak_memory.MeasurementError,
                    "output exceeded",
                ):
                    measure_peak_memory._run_bounded_process(
                        [sys.executable, "-c", noisy_child],
                        5,
                    )

    def test_worker_timeout_uses_fixed_bounded_diagnostic(self):
        with self.assertRaisesRegex(
            measure_peak_memory.MeasurementError,
            "worker timed out",
        ):
            measure_peak_memory._run_bounded_process(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                1,
            )

    def test_public_boundary_hides_unexpected_exception_details(self):
        marker = "private-temporary-path-marker"
        stderr = io.StringIO()
        with (
            mock.patch.object(
                measure_peak_memory,
                "_build_report",
                side_effect=OSError(marker),
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            result = measure_peak_memory.main(["--target-bytes", "16384"])

        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "error: measurement failed\n")
        self.assertNotIn(marker, stderr.getvalue())

    def test_partial_thread_start_baseexception_reaps_worker(self):
        captured = {}
        original_popen = measure_peak_memory.subprocess.Popen
        original_start = measure_peak_memory.threading.Thread.start
        start_count = 0

        def capture_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            captured["process"] = process
            return process

        def fail_second_start(thread):
            nonlocal start_count
            start_count += 1
            if start_count == 2:
                raise KeyboardInterrupt("synthetic thread start interruption")
            return original_start(thread)

        with (
            mock.patch.object(
                measure_peak_memory.subprocess,
                "Popen",
                side_effect=capture_process,
            ),
            mock.patch.object(
                measure_peak_memory.threading.Thread,
                "start",
                new=fail_second_start,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "start interruption"),
        ):
            measure_peak_memory._run_bounded_process(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                5,
            )

        self.assertIsNotNone(captured["process"].poll())

    def test_stdout_failure_is_inside_path_free_public_boundary(self):
        marker = "private-stdout-path-marker"
        stderr = io.StringIO()

        class FailingStdout:
            def write(self, _value):
                raise OSError(marker)

        with (
            mock.patch.object(
                measure_peak_memory,
                "_build_report",
                return_value={"schema_version": 1},
            ),
            mock.patch.object(sys, "stdout", FailingStdout()),
            mock.patch.object(sys, "stderr", stderr),
        ):
            result = measure_peak_memory.main(["--target-bytes", "16384"])

        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "error: measurement failed\n")
        self.assertNotIn(marker, stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
