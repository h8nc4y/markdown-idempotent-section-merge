# markdown-idempotent-section-merge

[![Validate](https://github.com/h8nc4y/markdown-idempotent-section-merge/actions/workflows/validate.yml/badge.svg)](https://github.com/h8nc4y/markdown-idempotent-section-merge/actions/workflows/validate.yml)

An agent skill for Claude Code and Codex: idempotently replace-or-append a
Markdown section without corrupting code fences or raw HTML blocks —
literal-region-aware heading scans, fixed-marker boundaries, single-H2
invariants, and an apply-twice-diff-zero verification recipe, backed by a
tested reference implementation.

## What It Solves

Agents (and scripts) constantly maintain one canonical `## Section` in
Markdown files they do not fully own — `README.md`, `AGENTS.md`,
`CLAUDE.md`, handbooks — on a "replace if present, append if missing" rule.
The classic implementation takes the replace range as "the `## X` line up
to the next `^##` match" and breaks in several measured ways:

- **A `## ...` line inside a fenced code block** (a report template, a
  sample document) is misread as the next section boundary. The range is
  cut mid-fence; the leftover closing fence re-opens as a new fence and
  swallows every section after it, the fenced literal escapes and renders
  as a duplicate-looking heading, and the merge never converges — each run
  grows the file.
- **A bare `^##` regex also matches `###`**, so a subheading ends the range
  early and old subsections survive below the new ones.
- **A `# ...` / `## ...` literal inside a CommonMark raw HTML block** can cut
  the range mid-`script`, `style`, `pre`, comment, or other HTML block,
  leaving its tail and closing syntax behind.

The skill documents two safe boundary methods (literal-region-aware scan,
fixed begin/end markers), the invariants that make replace-or-append well
defined (H1/H2 boundaries — the folk `^##[^#]` hardened, exactly one
heading per block, stop-and-report on malformed input), and a verification
recipe — apply twice and require a zero diff, literal-region-aware heading
count = 1, `git diff --stat` = one file.

None of this is hypothetical: the repository ships the fence-blind
implementation inside its test suite and proves the corruption on a fixture
(see [Reference Implementation](#reference-implementation)).

## Who It Is For

- Claude Code and Codex users whose agents keep sections of `AGENTS.md`,
  `CLAUDE.md`, or `README.md` up to date mechanically.
- Anyone scripting "update this section" Markdown automation who wants the
  failure modes documented — and tested — before hitting them.

## Install

Clone the repository:

```bash
git clone https://github.com/h8nc4y/markdown-idempotent-section-merge.git
cd markdown-idempotent-section-merge
```

### Claude Code

Claude Code auto-invokes the skill when a task matches the `description`
frontmatter. Install for your user account on shells with POSIX syntax:

```bash
dest="${HOME}/.claude/skills/markdown-idempotent-section-merge"
if [ -e "$dest" ]; then
  echo "Install target already exists: $dest"
else
  mkdir -p "$dest"
  cp SKILL.md "$dest/SKILL.md"
fi
```

Install for your user account from PowerShell:

```powershell
$dest = Join-Path $HOME '.claude\skills\markdown-idempotent-section-merge'
if (Test-Path -LiteralPath $dest) {
  throw "Install target already exists: $dest"
}
New-Item -ItemType Directory -Path $dest | Out-Null
Copy-Item -LiteralPath .\SKILL.md -Destination (Join-Path $dest 'SKILL.md')
```

Notes:

- If you set `CLAUDE_CONFIG_DIR`, replace `~/.claude` with that directory.
- To scope the skill to a single project instead, copy `SKILL.md` to
  `.claude/skills/markdown-idempotent-section-merge/SKILL.md` inside that
  project's repository.
- Optionally copy `scripts/merge_section.py` into the same skill folder so
  the agent can run the reference implementation directly instead of
  reimplementing it.

The existence guard is intentional: do not overwrite an already-installed
skill without reviewing the local copy first.

### Codex (agent skills)

Manual Codex-style skill install on shells with POSIX syntax:

```bash
dest="${HOME}/.agents/skills/markdown-idempotent-section-merge"
if [ -e "$dest" ]; then
  echo "Install target already exists: $dest"
else
  mkdir -p "$dest"
  cp SKILL.md "$dest/SKILL.md"
fi
```

Manual Codex-style skill install from PowerShell:

```powershell
$dest = Join-Path $HOME '.agents\skills\markdown-idempotent-section-merge'
if (Test-Path -LiteralPath $dest) {
  throw "Install target already exists: $dest"
}
New-Item -ItemType Directory -Path $dest | Out-Null
Copy-Item -LiteralPath .\SKILL.md -Destination (Join-Path $dest 'SKILL.md')
```

To scope the skill to a single project instead, copy `SKILL.md` to
`.agents/skills/markdown-idempotent-section-merge/SKILL.md` inside that
repository — Codex scans `.agents/skills` from the working directory up to
the repository root (per the official skills documentation).

If your agent reads skills from a different directory, check its
documentation and copy `SKILL.md` into the matching
`skills/markdown-idempotent-section-merge/` folder.

## Manual Use

Reach for the skill when you see one of these symptoms:

- A maintained section is duplicated after every automation run.
- A section merge cut the range short inside a fenced code block.
- A `## ...` line inside a code fence was treated as the next section.
- A `###` subheading ended the replace range early.
- The updater never converges: the second run still changes the file.
- After a merge, everything below some point renders as one giant code
  block (a leftover fence delimiter re-opened as a new fence).

Then follow [SKILL.md](SKILL.md): pick a boundary method (fence-aware scan
or fixed markers), enforce the single-H2 invariants, and verify with
apply-twice-diff-zero, a fence-aware heading count of 1, and a one-file
`git diff --stat`.

## Reference Implementation

[`scripts/merge_section.py`](scripts/merge_section.py) — dependency-free
Python 3, standard library only. Python over a shell for one reason that
matters here: explicit byte-level control of newlines and BOM makes
apply-twice-diff-zero provable, where shell text pipelines tend to
normalize line endings behind your back.

```bash
python scripts/merge_section.py TARGET.md SECTION.md            # merge in place
python scripts/merge_section.py TARGET.md SECTION.md --check   # drift check (exit 1 = stale)
```

`SECTION.md` holds the canonical block: its first line is exact `## Heading`
with one ASCII space between the opening hashes and nonempty content (or exact
`##` for an empty H2). Extra separator spaces/tabs are refused before the first
write. LF/CRLF style and a UTF-8 BOM are preserved byte-for-byte.
Changed content is flushed to an exclusive temporary file beside the target
and committed with one atomic path replacement, so readers never observe a
partially written document. Before content is written, a new temporary starts
as mode `0600` on POSIX or with a protected SYSTEM/Owner-Rights-only DACL on
Windows; its identity, bytes, metadata, and Windows DACL are verified again
before commit. An existing POSIX target retains owner/group, permission bits,
and a bounded set of extended attributes. For an existing Windows target,
`ReplaceFileW` carries forward the documented DACL, file attributes, and named
streams through a private recovery backup. A missing target keeps the private
temporary's permissions after installation.

Both `TARGET.md` and `SECTION.md` are read as no-follow snapshots of ordinary
single-link files. Symbolic links, Windows reparse points, EFS-encrypted Windows
targets, non-regular files, and multi-hard-link files are refused because
reading or replacing them would silently change their semantics or expose a
plaintext temporary.

Input snapshots count raw bytes, including a UTF-8 BOM and LF/CRLF bytes.
`TARGET.md` may be at most 8 MiB (8,388,608 bytes), and `SECTION.md` may be at
most 2 MiB (2,097,152 bytes); an exact-limit file is accepted. Each descriptor
read requests at most its limit plus one byte so an over-limit input fails
closed with exit code 2 and a fixed, path-free diagnostic before any write.
The 8 MiB target budget is applied again during the pre-commit conflict
recheck. Replacement and recovery snapshots use the already-known expected
payload length rather than an unbounded read. The same no-write rejection
applies to normal merge and `--check`.

After the initial target and canonical-block snapshots, raw LF bytes are
counted before the respective UTF-8 decoding: `TARGET.md` may contain at most
1,000,000 newlines and `SECTION.md` at most 250,000. A CRLF ending contains one
LF byte and therefore counts once; a BOM does not affect the count. Exact-limit
input is accepted, while limit-plus-one input exits 2 with a fixed, path-free
diagnostic before that input's UTF-8 decoding or any write.

The final merged raw output is independently limited to the same 8 MiB after
the UTF-8 BOM and selected LF/CRLF endings are restored. An exact-limit output
is written and remains a byte-identical no-op on the next run. A limit-plus-one
append, replacement, or normalization fails with exit code 2 and a fixed,
path-free diagnostic before a temporary file is created, in both normal merge
and `--check`. This closure prevents the tool from producing a target that its
next invocation must reject as oversized.

A document-leading exact YAML frontmatter block (`---` through exact `---` or
`...`) or TOML block (`+++` through exact `+++`) is excluded from heading and
fence scans. Heading-looking metadata comments therefore cannot become
replacement anchors. A recognized opener without its exact closer is refused
before any write. The exact scope and regression plan are recorded in
[the frontmatter heading-scan contract](docs/frontmatter-heading-scan-contract.md).

Top-level, column 0–3 CommonMark 0.31.2 raw HTML block types 1–7 are also
excluded from heading and setext scans. Fence and HTML states are processed by
one exclusive state machine, so a delimiter inside the other literal region
cannot leak state. For mutation safety, recognized type 1–5 blocks require
their explicit end condition; ambiguous type 7 context fails closed. The
tag/attribute alphabet remains ASCII-only; Unicode case-fold lookalikes never
open or close an ASCII HTML block. A possible link reference definition
followed by `===` is also refused when a partial scan cannot prove whether
setext ended the paragraph; this includes CommonMark link labels that span
multiple lines. The label state is escape-aware: a trailing backslash keeps a
possible label open, while the first unescaped `]` without an immediate colon
returns the text to ordinary paragraph handling. The
precise supported profile and intentional CommonMark deviation are recorded in
[the raw HTML heading-scan contract](docs/html-block-heading-scan-contract.md).

The target's identity, metadata, and bytes are rechecked immediately before
commit. This detects changes completed before that check, but the check and
replacement are separate operations: a writer that changes an existing target
in the final window can still be overwritten. Serialize every writer with an
external lock or a single runner when lost-update prevention is required.
A previously missing target is installed with a no-replace operation, so a
concurrent creation is never overwritten.

Fixtures under [`tests/fixtures/`](tests/fixtures) cover the trap case
(heading inside a code fence), heading literals in leading frontmatter and
raw HTML, append, replace, the `###` subheading boundary, and the `#` part
boundary.
The self-test needs no dependencies:

```bash
python scripts/test_merge_section.py
```

It checks the contract (expected output, apply-twice-diff-zero, heading
count = 1, CRLF/BOM stability) and keeps the trap *measured*: the suite
ships the fence-blind implementation (`fence_blind_merge`) and asserts that
it corrupts the trap fixture — the following `## License` section is
swallowed by a re-opened fence (fence-aware heading count drops to 0), the
fenced `## Weekly report` literal escapes its fence and renders as a real
heading, the output is not idempotent, and the range is cut at `###` on the
subheading fixture. Those assertions passing is the recorded measurement of
the naive implementation failing.

## Synthetic Examples

- [Before / after](examples/before-after.md) — the trap fixture walked
  through: correct output next to the naive corruption, annotated.
- [Verification recipe](examples/verification-recipe.md) — the three
  post-merge checks as copy-paste commands, including CI drift checking
  with `--check`.

The examples use placeholders only. Do not replace them with secrets, real
repository paths you cannot publish, or customer data in public issues.

## 日本語概要 (Japanese Overview)

「見出し `## X` の節を、既存があれば置換・無ければ追記」という Markdown 節の
冪等マージを安全に行うための skill です。核心の罠: 置換範囲を「`## X` 行〜
次の `^##` 行の直前」で取ると、節本文のコードフェンス内にある `## ...`
リテラル行を「次の見出し」と誤認して範囲を途中で切ります。取り残された
閉じフェンスが再オープンして後続の節を丸ごと飲み込み、マージは冪等では
なくなります（実行のたびにファイルが成長）。裸の `^##` は `###` にも
マッチするため、小見出しでも範囲が早期切断されます。
raw HTML block 内の疑似 H1/H2 でも同じ早期切断が起きるため、参照実装は
frontmatter・fence・CommonMark HTML type 1〜7 を排他的に走査します。

- 安全な境界2方式: frontmatter/fence/raw HTML 状態を追跡する見出し走査
  （literal region 内は見出しと数えない）、または固定 begin/end マーカー
- 不変条件: 境界は `^##[^#]`（`###` を除外）、正本ブロック内の H1/H2
  見出しは先頭の ATX H2 ちょうど1個（possible setext も書込み前に拒否）
- 検証レシピ: 同じマージを2回当てて `git diff` が空（apply-twice-diff-zero）、
  見出し出現数 = 1、`git diff --stat` が対象1ファイルのみ
- テスト済み参照実装（Python 標準ライブラリのみ）と、素朴実装が実際に
  壊れることを実測する反証テストつき

日本語の完全版は [docs/SKILL.ja.md](docs/SKILL.ja.md) にあります。
インストールは上記の手順どおり、`SKILL.md` を Claude Code なら
`~/.claude/skills/markdown-idempotent-section-merge/` へ、Codex なら
`~/.agents/skills/markdown-idempotent-section-merge/` へコピーしてください。

## Safety Notes

- The merge writes exactly one file per invocation; verify with
  `git diff --stat` and keep targets under version control so a bad merge
  is recoverable.
- If the target already contains duplicate copies of the heading, the
  reference implementation stops and reports instead of guessing which copy
  to keep.
- Documents (or blocks) that end inside an unclosed code fence are refused
  as malformed input — CommonMark runs such a fence to EOF, so a merge
  would silently rewrite the visually swallowed tail. The same
  stop-and-report rule covers CR-only line endings and a possible setext
  heading inside the canonical block or replaced span. Rejecting the block
  before its first append keeps validation stable on every run. See
  [`docs/setext-block-heading-contract.md`](docs/setext-block-heading-contract.md).
- Recognized raw HTML types 1–5 must reach their explicit end condition in
  both target and block. CommonMark itself permits EOF termination, but this
  stricter mutation rule prevents an appended managed H2 from being swallowed
  by raw HTML. Ambiguous type 7 context is refused as well.
- A leading exact `---` (YAML) or `+++` (TOML) frontmatter opener must have an
  exact matching closer. The scanner ignores heading literals inside a closed
  block and fails closed on an unclosed one rather than confusing metadata
  with the managed section.
- CommonMark block whitespace is ASCII space/tab only. Unicode whitespace such
  as NBSP or EM SPACE remains content in heading, blank-line, setext, fence,
  and closing-hash decisions; it is never silently trimmed into another
  structure. See
  [`docs/commonmark-ascii-whitespace-contract.md`](docs/commonmark-ascii-whitespace-contract.md).
- CommonMark strips leading ASCII space/tab from ATX heading raw content.
  Therefore a target `##  Name` or `##	Name` is a semantic alias of canonical
  `## Name`, not a separate section. Noncanonical block separators and matching
  target aliases fail closed before writing, including indentation and
  closing-hash combinations. See
  [`docs/managed-heading-separator-contract.md`](docs/managed-heading-separator-contract.md).
- When only mixed line endings are normalized, the tool reports
  `normalized` / `would-normalize` — it never claims "unchanged" while
  rewriting bytes.
- Recoverable replacement failures restore or retain the verified original.
  `AtomicCommitError.committed` is `True`, `False`, or `None` when the observed
  state cannot prove either outcome; named backup/temporary artifacts are
  retained for manual recovery when cleanup or state is uncertain. POSIX
  missing-target creation likewise reports `committed=True` and retains its
  extra hard-link name if post-link cleanup fails.
  The recovery behavior follows the documented
  [`ReplaceFileW`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)
  failure states.
- Recovery cleanup compares file identity immediately before unlinking.
  Because portable path-based unlink is not conditional on that identity, a
  final name-swap race remains; unpredictable private names reduce exposure,
  and ambiguous artifacts are retained instead of guessed away.
- Changes visible before the final recheck stop the merge. The recheck is
  best-effort, not compare-and-swap; externally serialize writers when an
  existing target must never suffer a lost update. Concurrent creation of a
  missing target is protected by no-replace commit.
- Never paste tokens, credentials, private logs, or customer data into
  issues, examples, or fixture files.

## Limitations

- The managed heading must be a plain `## Name` at column 0 with exactly one
  ASCII separator space before nonempty content; exact `##` remains the empty
  H2 form. Extra separator spaces/tabs in the block are rejected. A matching
  separator alias outside literal regions makes both merge and `--check`
  refuse without writing. See
  [`docs/managed-heading-separator-contract.md`](docs/managed-heading-separator-contract.md).
- A closing-hash block heading is rejected, and a matching `## Name ##` form
  outside literal regions is treated as an ambiguous identity: both merge and
  `--check` refuse without writing. See
  [`docs/closing-hash-managed-heading-contract.md`](docs/closing-hash-managed-heading-contract.md).
- A matching managed H2 indented by 1–3 ASCII spaces outside literal regions
  is treated as an ambiguous identity and makes both merge and `--check`
  refuse without writing. The tool does not auto-reindent possible list or
  container content. See
  [`docs/indented-managed-heading-contract.md`](docs/indented-managed-heading-contract.md).
- Setext headings are never boundaries; a possible setext heading inside
  the canonical block or replaced span makes the merge refuse instead of
  accepting it once and failing on the next run, or deleting it during
  replacement. See
  [`docs/setext-block-heading-contract.md`](docs/setext-block-heading-contract.md).
- Frontmatter recognition is intentionally narrow: only exact column-0
  delimiters on the first line are supported (YAML `---` with `---`/`...`, or
  TOML `+++` with `+++`). Later or non-exact delimiter lines remain ordinary
  Markdown.
- Fence handling covers CommonMark's core column 0–3 backtick/tilde rules
  (including the backtick-info-string exclusion); fences inside blockquotes
  or deep list indentation are out of scope.
- Raw HTML handling covers top-level, column 0–3 CommonMark 0.31.2 block
  types 1–7. Container prefixes, lazy continuations, and 4+ character
  indentation are not fully parsed; a possible type 7 start after such an
  unknown context fails closed. Tag and attribute names use CommonMark's
  ASCII grammar rather than Unicode case-fold matching. Possible-reference
  plus setext ambiguity, including a multi-line link label, is likewise
  refused.
- UTF-8 with LF or CRLF only; CR-only endings are refused, and files mixing
  CRLF and LF are normalized to CRLF on the first write (reported as
  `normalized`).
- Raw target input and final merged output are limited to 8 MiB; raw
  canonical-block input is limited to 2 MiB. All limits include BOM and
  line-ending bytes. Raw newline counts are independently limited to 1,000,000
  for the target and final output, and 250,000 for the canonical block. CRLF
  counts once through its LF byte. Exact limits succeed, including an exact
  final-output limit that is a no-op on the next run; limit-plus-one input
  fails before that input's decoding, and limit-plus-one output fails before
  temporary-file creation, with fixed path-free diagnostics. These limits bound snapshot I/O
  and persisted output, not
  Python peak memory: UTF-8 decoding, line/state lists, output construction,
  and CRLF normalization can use more memory than the raw inputs. The synthetic
  8 MiB characterization measured a 230.06 MiB median process peak for the
  densest LF replacement on Windows / CPython 3.11; this is descriptive, not a
  cross-platform guarantee. See
  [the peak-memory characterization](docs/peak-memory-characterization.md).
  The runtime has no streaming parser, so the line-count budget reduces the
  worst accepted density but is not a strict process-memory guarantee.
- The reference implementation accepts a missing target or an existing
  ordinary file with one hard link. Symbolic links and multi-hard-link files
  are refused; choose the intended ordinary file path explicitly.
- Existing Windows targets must be owned by the effective token's default
  owner. `ReplaceFileW` is relied on only for its documented DACL, attribute,
  and named-stream behavior: exact owner, group, or SACL preservation is not
  promised. Do not use this reference implementation when those fields must be
  preserved exactly. EFS-encrypted targets are refused before creating a
  plaintext temporary.

## Non-Goals

- No general Markdown parser or renderer; the scope is one maintained
  section's boundary, invariants, and verification.
- No multi-section batch mode — one file, one section per invocation keeps
  the verification sharp.

## Validation

Run the full local validation from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-oss-readiness.ps1
python scripts\test_merge_section.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-scan-private-markers.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\scan-private-markers.ps1
```

If `pwsh` is available, the same checks can be run with:

```powershell
pwsh -NoProfile -File .\scripts\validate-oss-readiness.ps1
python scripts\test_merge_section.py
pwsh -NoProfile -File .\scripts\test-scan-private-markers.ps1
pwsh -NoProfile -File .\scripts\scan-private-markers.ps1
```

On macOS, Linux, or any POSIX shell with PowerShell 7 (`pwsh`) installed:

```bash
pwsh -NoProfile -File ./scripts/validate-oss-readiness.ps1
python3 scripts/test_merge_section.py
pwsh -NoProfile -File ./scripts/test-scan-private-markers.ps1
pwsh -NoProfile -File ./scripts/scan-private-markers.ps1
```

The CI suite runs a 64 KiB subprocess/schema regression through
`test_merge_section.py`. The current health-check matrix defaults to 1 MiB so
all dense-line cases remain inside the production newline budget:

```powershell
python scripts/measure_peak_memory.py --repetitions 1 --timeout-seconds 60
```

The command uses synthetic data only and emits one path-free JSON record.
The documented exact-8-MiB matrix is historical evidence from commit
`da8991d`, before the newline budget was added. Explicit 8 MiB dense fixtures
on current code are intentionally rejected; reproduce the historical matrix
only from that commit in an isolated checkout. Environment-specific results
and metric limitations are documented in
[the peak-memory characterization](docs/peak-memory-characterization.md).

The Windows ownership-transfer regression deliberately replaces the live
target-metadata guard with an exact-argument spy. That test therefore measures
only the `_commit_temporary` ownership boundary and retained recovery artifact,
without depending on a runner filesystem's timestamp behavior. A separate
cross-platform regression sets the target mtime through
[`os.utime(ns=...)`](https://docs.python.org/3.14/library/os.html#os.utime),
without changing its bytes, proves that the production fingerprint differs,
then proves the real guard refuses the write before the commit helper is called.
The production fingerprint remains strict. Python documents that the meaning
and resolution of
[`os.stat_result` timestamps](https://docs.python.org/3.14/library/os.html#os.stat_result)
depend on the OS and filesystem, even though the `*_ns` fields are integer
nanosecond representations.

Bounded POSIX child cleanup uses the system `setsid` executable when
available and a same-host `libc` `setsid(2)` gate otherwise. The self-test
forces the fallback path, so macOS does not require an extra `setsid`
package merely to run the scanner.

Also run Git whitespace checks on your working changes before publishing:

```bash
git diff --check
```

The GitHub Actions workflow runs the same validation, the reference
implementation tests, the scan self-test, the private-marker scan, and a
whitespace check on Windows, Ubuntu, and pinned GitHub-hosted macOS 15 for
pull requests and pushes to `main`. The Windows job runs the scanner checks
under both PowerShell 7 and Windows PowerShell 5.1. Scanner PowerShell sources
use UTF-8 with BOM so their Japanese intent comments parse consistently in
both hosts. Each matrix job has a 25-minute timeout so the Windows PowerShell
7 and 5.1 suites can finish sequentially under one bounded job.
The Python setup step is pinned to the official `setup-python` v7.0.0
immutable release commit, whose action runtime is Node.js 24. The exact pin,
validator mutations, and CI annotation acceptance criteria are documented in
[the setup-python Node.js 24 pin contract](docs/setup-python-node24-pin-contract.md).
The checkout step is pinned to the official v7.0.1 verified full commit SHA,
whose action runtime remains Node.js 24, and sets `persist-credentials: false`;
later steps only inspect committed content and never need an authenticated Git
operation. The provenance, preserved workflow boundaries, and validator
mutations are documented in
[the checkout v7.0.1 pin contract](docs/checkout-v7-upgrade.md).

## Contributing

Contributions are welcome when they make the merge discipline safer,
clearer, or easier to verify. Read [CONTRIBUTING.md](CONTRIBUTING.md)
before opening a pull request.

Keep all examples synthetic. Do not include tokens, credentials, private
repository names, internal absolute paths, or customer data.

For local-only private markers, create an untracked
`.private-markers.local` file with one literal marker per line, or set
`MARKDOWN_IDEMPOTENT_SECTION_MERGE_PRIVATE_MARKERS` with newline-separated
markers. The scanner reads these values but does not print the matched
marker.

## Security

If you find unsafe guidance or accidental private-data exposure, follow
[SECURITY.md](SECURITY.md) and use private reporting for sensitive details.

## License

MIT. See [LICENSE](LICENSE).
