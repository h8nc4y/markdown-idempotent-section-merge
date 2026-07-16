# Changelog

All notable changes to this project are documented in this file.

The format loosely follows Keep a Changelog conventions.

## Unreleased

## 0.1.0 - 2026-07-16

### Added

- Initial markdown-idempotent-section-merge skill (`SKILL.md`): the
  fenced-heading trap (a `## ...` literal inside a code fence misread as
  the next section boundary, and `###` subheadings matched by bare `^##`),
  two safe boundary methods (fence-aware heading scan and fixed begin/end
  markers), the merge invariants (H1/H2 boundaries hardening the folk
  `^##[^#]` form, exactly one heading per block, at most one heading copy
  in the target, canonical separator shape, no unclosed fences, no setext
  headings inside the replaced span), and the verification recipe
  (apply-twice-diff-zero, fence-aware heading count = 1, one-file
  `git diff --stat`).
- Japanese full version of the skill (`docs/SKILL.ja.md`).
- Reference implementation `scripts/merge_section.py` (Python 3, standard
  library only): fence-aware scanning with CommonMark's space rule and
  backtick-info-string exclusion, replace-or-append, `--check` drift mode
  with diff-style exit codes, byte-for-byte LF/CRLF and UTF-8 BOM
  preservation, honest `normalized` reporting for mixed line endings, and
  stop-and-report refusals for duplicate headings, unclosed fences,
  CR-only line endings, and possible setext headings in the span.
- Fixtures for the trap case (heading inside a code fence), append,
  replace, the `###` subheading boundary, and the `#` part boundary, plus
  the dependency-free self-test `scripts/test_merge_section.py` —
  including measured falsification of the fence-blind implementation
  (document corruption, non-idempotence, early cut at `###`).
- Synthetic examples: the trap fixture walked through before/after, and a
  one-page verification recipe.
- Private-marker scan for common secret prefixes, private-looking absolute
  paths, and non-allowlisted GitHub repository URLs, with a self-test and
  local marker support through `.private-markers.local` or the
  `MARKDOWN_IDEMPOTENT_SECTION_MERGE_PRIVATE_MARKERS` environment variable.
- OSS readiness validation script for required public project files and
  skill frontmatter.
- GitHub Actions workflow for validation, reference implementation tests,
  private-marker scanning, and whitespace checks, on both windows-latest
  and ubuntu-latest.
- Issue and pull request templates with sanitized-report guidance.
- Contributor, security, code of conduct, editor, and Git attribute
  documentation.
