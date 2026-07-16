#!/usr/bin/env python3
"""Self-test for merge_section.py. Standard library only:

    python scripts/test_merge_section.py

Per fixture under tests/fixtures/ it verifies that the merged output equals
expected.md byte-for-byte, that applying the same merge twice is a no-op
(apply-twice-diff-zero), and that the section heading occurs exactly once
outside code fences afterwards.

It also proves the skill's trap is real, not hypothetical: a fence-blind
``^##`` implementation (kept here as ``fence_blind_merge``) corrupts the
trap fixture, is not idempotent, and cuts a section short at a ``###``
subheading.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import merge_section  # noqa: E402  (import after sys.path setup)

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
    """Fence-aware count of lines equal to HEADING outside code fences."""
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
            "h1-boundary",
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
                heading = section.split("\n", 1)[0].rstrip()
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


class BoundaryHardeningTests(unittest.TestCase):
    """Boundary rules beyond the folk ``^##[^#]`` form, each measured."""

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
