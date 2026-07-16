# markdown-idempotent-section-merge

[![Validate](https://github.com/h8nc4y/markdown-idempotent-section-merge/actions/workflows/validate.yml/badge.svg)](https://github.com/h8nc4y/markdown-idempotent-section-merge/actions/workflows/validate.yml)

An agent skill for Claude Code and Codex: idempotently replace-or-append a
Markdown section without corrupting code fences — fence-aware heading scans,
fixed-marker boundaries, single-H2 invariants, and an apply-twice-diff-zero
verification recipe, backed by a tested reference implementation.

## What It Solves

Agents (and scripts) constantly maintain one canonical `## Section` in
Markdown files they do not fully own — `README.md`, `AGENTS.md`,
`CLAUDE.md`, handbooks — on a "replace if present, append if missing" rule.
The classic implementation takes the replace range as "the `## X` line up
to the next `^##` match" and breaks in two measured ways:

- **A `## ...` line inside a fenced code block** (a report template, a
  sample document) is misread as the next section boundary. The range is
  cut mid-fence; the leftover closing fence re-opens as a new fence and
  swallows every section after it, the fenced literal escapes and renders
  as a duplicate-looking heading, and the merge never converges — each run
  grows the file.
- **A bare `^##` regex also matches `###`**, so a subheading ends the range
  early and old subsections survive below the new ones.

The skill documents two safe boundary methods (fence-aware heading scan,
fixed begin/end markers), the invariants that make replace-or-append well
defined (H1/H2 boundaries — the folk `^##[^#]` hardened, exactly one
heading per block, stop-and-report on malformed input), and a verification
recipe — apply twice and require a zero diff, fence-aware heading
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

`SECTION.md` holds the canonical block: its first line is the exact
`## Heading`. LF/CRLF style and a UTF-8 BOM are preserved byte-for-byte.

Fixtures under [`tests/fixtures/`](tests/fixtures) cover the trap case
(heading inside a code fence), append, replace, the `###` subheading
boundary, and the `#` part boundary. The self-test needs no dependencies:

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

- 安全な境界2方式: コードフェンス状態を追跡する見出し走査（``` の内側は
  見出しと数えない）、または固定 begin/end マーカー
- 不変条件: 境界は `^##[^#]`（`###` を除外）、正本ブロック内の H2 見出しは
  ちょうど1個
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
  heading inside the replaced span.
- When only mixed line endings are normalized, the tool reports
  `normalized` / `would-normalize` — it never claims "unchanged" while
  rewriting bytes.
- Never paste tokens, credentials, private logs, or customer data into
  issues, examples, or fixture files.

## Limitations

- The managed heading must be a plain `## Name` at column 0 (closing-hash
  headings work as boundaries but match by exact line, so `## X ##` and
  `## X` are different headings).
- Setext headings are never boundaries; a possible setext heading inside
  the replaced span makes the merge refuse instead of deleting it.
- Fence handling covers CommonMark's core column 0–3 backtick/tilde rules
  (including the backtick-info-string exclusion); fences inside blockquotes
  or deep list indentation are out of scope.
- UTF-8 with LF or CRLF only; CR-only endings are refused, and files mixing
  CRLF and LF are normalized to CRLF on the first write (reported as
  `normalized`).

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

Also run Git whitespace checks on your working changes before publishing:

```bash
git diff --check
```

The GitHub Actions workflow runs the same validation, the reference
implementation tests, the scan self-test, the private-marker scan, and a
whitespace check on pull requests and pushes to `main`.

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
