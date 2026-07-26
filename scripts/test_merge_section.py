#!/usr/bin/env python3
"""Self-test for merge_section.py. Standard library only:

    python scripts/test_merge_section.py

Per fixture under tests/fixtures/ it verifies that the merged output equals
expected.md byte-for-byte, that applying the same merge twice is a no-op
(apply-twice-diff-zero), and that the section heading occurs exactly once
outside leading frontmatter and code fences afterwards.

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
    """Count HEADING outside leading frontmatter and code fences."""
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
        temporary = self.dir / ".target.md.pending"
        original = b"# Doc\n\nold.\n"
        replacement = b"# Doc\n\nnew.\n"
        target.write_bytes(original)
        temporary.write_bytes(replacement)
        return target, temporary, target.lstat(), original, replacement

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
        target, temporary, target_stat, original, replacement = (
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
            )

        self.assertEqual(target.read_bytes(), replacement)
        self.assertFalse(temporary.exists())
        self.assertFalse(captured["backup"].exists())
        self.assertEqual(self._files(), {"target.md"})

    @unittest.skipIf(os.name == "nt", "POSIX FIFOs are not portable")
    def test_windows_commit_rejects_fifo_swap_before_private_temp_read(self):
        target, temporary, target_stat, original, _replacement = (
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
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (temporary,))
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(stat.S_ISFIFO(temporary.lstat().st_mode))

    def test_windows_commit_success_retains_backup_when_cleanup_fails(self):
        target, temporary, target_stat, original, replacement = (
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
                )

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (captured["backup"],))
        self.assertEqual(target.read_bytes(), replacement)
        self.assertEqual(captured["backup"].read_bytes(), original)
        self.assertFalse(temporary.exists())

    def test_windows_commit_success_retains_recovery_when_target_is_unverified(self):
        target, temporary, target_stat, original, replacement = (
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
        target, temporary, target_stat, original, _replacement = (
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
                temporary = case_dir / ".target.md.pending"
                original = b"# Doc\n\nold.\n"
                target.write_bytes(original)
                temporary.write_bytes(b"# Doc\n\nnew.\n")

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
                        )

                self.assertEqual(target.read_bytes(), original)
                self.assertFalse(temporary.exists())
                self.assertEqual(
                    {path.name for path in case_dir.iterdir()},
                    {"target.md"},
                )

    def test_windows_error_1176_retains_artifacts_when_target_changed(self):
        target, temporary, target_stat, original, replacement = (
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
        target, temporary, target_stat, original, replacement = (
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
                )

        self.assertTrue(caught.exception.committed)
        self.assertFalse(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (captured["backup"],))
        self.assertEqual(target.read_bytes(), replacement)
        self.assertEqual(captured["backup"].read_bytes(), original)

    def test_windows_error_reports_unresolved_state_as_unknown(self):
        target, temporary, target_stat, original, replacement = (
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
        target, temporary, target_stat, original, _replacement = (
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
                )

        self.assertEqual(caught.exception.errno, 1177)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(temporary.exists())
        self.assertFalse(captured["backup"].exists())
        self.assertEqual(self._files(), {"target.md"})

    def test_windows_error_1177_restore_interrupt_is_reconciled(self):
        target, temporary, target_stat, original, _replacement = (
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
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, ())
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(temporary.exists())
        self.assertFalse(captured["backup"].exists())

    def test_windows_error_1177_does_not_overwrite_new_target(self):
        target, temporary, target_stat, original, replacement = (
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
        target, temporary, target_stat, original, _replacement = (
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
        target, temporary, target_stat, original, replacement = (
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
                )

        self.assertFalse(caught.exception.committed)
        self.assertTrue(caught.exception.recovered)
        self.assertEqual(caught.exception.artifacts, (temporary,))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(temporary.read_bytes(), replacement)

    @unittest.skipUnless(os.name == "nt", "ownership transfer is Windows-only")
    def test_atomic_write_does_not_delete_temp_after_commit_ownership_transfer(self):
        target, unused, target_stat, original, replacement = (
            self._existing_commit()
        )
        unused.unlink()
        captured = {}

        def fail_after_ownership_transfer(
            _target,
            temporary,
            _target_stat,
            _original_bytes,
            _replacement_stat,
            _replacement_bytes,
        ):
            captured["temporary"] = temporary
            raise merge_section.AtomicCommitError(
                "synthetic ambiguous commit",
                committed=False,
                recovered=False,
                artifacts=(temporary,),
            )

        with mock.patch.object(
            merge_section,
            "_commit_temporary",
            side_effect=fail_after_ownership_transfer,
        ):
            with self.assertRaises(merge_section.AtomicCommitError):
                merge_section._atomic_write(
                    target,
                    replacement,
                    target_stat,
                    original,
                )

        self.assertTrue(captured["temporary"].exists())
        self.assertEqual(captured["temporary"].read_bytes(), replacement)
        self.assertEqual(target.read_bytes(), original)


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
            target
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

        with self.assertRaisesRegex(merge_section.MergeError, "symbolic link"):
            merge_section.merge_file(target, block)

        self.assertTrue(target.is_symlink())
        self.assertEqual(
            referent.read_bytes(),
            b"# Doc\n\n## Notes\n\nold.\n",
        )

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
