# Changelog

All notable changes to this project are documented in this file.

The format loosely follows Keep a Changelog conventions.

## Unreleased

### Changed

- Refuse a CommonMark closing-hash alias of the managed H2 before either
  merge or `--check` can append a semantic duplicate. Require the incoming
  block heading to use the plain form, include 0–3-space target aliases,
  ignore literal regions, preserve CRLF/BOM bytes on refusal, and keep
  non-closing hash text outside this bounded identity rule.
- Limit every CommonMark block-whitespace decision to ASCII space/tab instead
  of Python's broader Unicode `strip` semantics. Preserve NBSP, EM SPACE,
  form feed, and vertical tab as heading/line content; do not accept them as
  fence-closing or closing-hash suffix whitespace; and keep BOM/CRLF bytes
  unchanged on fail-closed CLI paths.
- Validate the documented POSIX path on pinned GitHub-hosted macOS 15 in
  addition to Windows and Ubuntu, without changing the existing 25-minute
  job bound, immutable action revisions, or Windows PowerShell 5.1 scope.
  Structurally pin the matrix-to-runner binding and the Windows-only PS5.1
  step inside the root `jobs` mapping, with in-memory mutations that reject
  block-scalar decoys, detached or noncanonically nested jobs, alternate YAML
  key spellings, multiline scalar decoys, and duplicate contract or root
  `jobs` keys. Pin every required validation step and its ordering as one
  executable contract rather than accepting command-name text elsewhere.
- Determine repository-root identity from one bounded Git probe that requires
  exact `--is-inside-work-tree=true` and an empty `--show-prefix`. This accepts
  equivalent ancestor-resolved path spellings such as macOS `/var/...` and
  `/private/var/...`, while repository subdirectories, bare/Git directories,
  malformed probe records, and non-worktree roots still fail closed.
- Treat a managed H2 with 1–3 leading ASCII spaces outside literal regions as
  an ambiguous identity and refuse both merge and `--check` without writing.
  Do not auto-reindent possible list/container content; preserve 4+ space,
  leading-tab, closing-hash, and literal-region behavior.
- Exclude top-level, column 0–3 CommonMark 0.31.2 raw HTML block types 1–7
  from heading and setext scans through one fence/HTML-exclusive state
  machine. Preserve type 7's no-paragraph-interruption rule, refuse ambiguous
  container/indent context, and require explicit end conditions for types 1–5
  before mutation. Keep case-insensitive tag/attribute matching ASCII-only so
  Unicode case-fold lookalikes cannot hide a real boundary or close type 1.
  Refuse the possible-link-reference + `===` context where a partial parser
  cannot safely decide whether type 7 may start, including CommonMark link
  labels that span lines. Track escaped brackets and line-ending backslashes,
  and release the possible-definition state at an unescaped `]` not followed
  by a colon, while preserving the simple no-blank definition-plus-tag
  paragraph rule.
  Add full-output, apply-twice, CLI no-write, CRLF/BOM, and fence/HTML
  interaction regressions.
- Exclude exact document-leading YAML (`---` through `---`/`...`) and TOML
  (`+++` through `+++`) frontmatter from heading/fence scans, so metadata
  comments cannot be mistaken for the managed section. Refuse unclosed
  frontmatter before writing, while preserving non-leading thematic breaks,
  LF/CRLF, UTF-8 BOM, CLI exit-code, and apply-twice contracts.
- Write merged Markdown through an exclusive same-directory temporary file,
  flush it, and commit with one atomic path replacement. Start private
  temporaries at POSIX mode `0600` or a protected Windows
  SYSTEM/Owner-Rights-only DACL, then recheck identity, bytes, metadata, and the
  Windows DACL before commit. Preserve bounded POSIX
  owner/group/mode/extended attributes; use only the documented Windows
  `ReplaceFileW` DACL/file-attribute/named-stream behavior and reject
  EFS-encrypted or differently owned Windows targets. Read target and block
  through no-follow snapshots and refuse symbolic-link, Windows reparse-point,
  non-regular, or multi-hard-link inputs before content is read.
  Existing-target Windows commits now use a private recovery backup, reconcile
  documented and interruption partial states, and retain ambiguous artifacts
  with tri-state commit/recovery status. POSIX missing-target creation uses an
  atomic no-replace link, reports committed cleanup partials, and retains the
  extra artifact. Rechecks remain best-effort rather than compare-and-swap, and
  identity-before-unlink cleanup retains a documented final path-name race.
- Make the private-marker scanner hermetic and bounded across Windows and
  POSIX. It now isolates every Git child from ambient `GIT_*` state, hooks,
  filters, attributes, templates, traces, replace objects, lazy fetching,
  and external transport protocols.
- Scan the union of the exact index blob and the current regular worktree
  snapshot. Read intent-to-add from extended index flags, reject conflicts,
  gitlinks, symlink/reparse ancestry, missing blobs, malformed metadata,
  and raw stage/debug drift immediately before success.
- Read index content through one bounded `git cat-file --batch` stream and
  impose finite budgets on child runtime/output/processes, filesystem
  entries, scan targets, bytes, lines, regex matches, findings, and display
  output. Invalid root-level or ancestor `.git` metadata fails closed, while
  nested `.git` directories and leaf `.git` files inside a confirmed non-Git
  fallback root remain excluded as Git control metadata.
- Create the Windows target suspended, assign it atomically to a kill-on-close
  Job, then resume it without a text-decoding wrapper. Isolate POSIX descendants
  in a process group via `setsid` or an errno-aware same-host `libc` fallback.
  Launch-failure cleanup now verifies termination and bounded wait results.
- Escape control, bidi/format, and logical line-separator characters in
  diagnostics. Hostile nonexistent paths fail with a fixed code. Finding
  reports are emitted as one explicit UTF-8 payload whose prefix, header,
  rows, and platform newlines all share the 16 KiB byte budget.
- Treat `.env` variants, PEM/certificate/key files, `.npmrc`, and
  extensionless text as scan candidates while preserving Unix-hidden
  dotfile enumeration in both tracked and working-tree fallback modes.
- Preserve the repository-only GitHub URL allowlist: this repository URL
  remains accepted, while every other repository URL remains a finding.
- Run scanner validation on Windows and Ubuntu, add Windows PowerShell 5.1
  coverage, keep Japanese intent comments in UTF-8 with BOM, and bound each
  CI matrix job to 25 minutes so both Windows hosts finish sequentially.
- Pin GitHub Actions dependencies to full commit SHAs while retaining their
  reviewed major-version annotations, and assert the exact revisions in the
  readiness gate.

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
