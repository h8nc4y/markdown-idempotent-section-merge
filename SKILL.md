---
name: markdown-idempotent-section-merge
description: >-
  Idempotently replace-or-append one Markdown section (a "## Heading" block)
  without corrupting code fences. Use on symptoms like: an automated section
  merge duplicated the section on every run, a replace range was cut short at
  a "##" line inside a code fence, a heading inside a code fence was treated
  as the next section boundary, a "###" subheading ended the section early, a
  README / AGENTS.md / CLAUDE.md updater never converges (the second run
  still changes the file), or a leftover fence swallowed the rest of the
  document after a bad merge. Covers fence-aware heading scanning, fixed
  begin/end marker boundaries, the single-H2 invariant (^##[^#]), and an
  apply-twice-diff-zero verification recipe with a tested reference
  implementation.
---

# Markdown Idempotent Section Merge

Procedure for maintaining one canonical `## Heading` section in a Markdown
document under automation: replace the section when it exists, append it
when it does not, and produce byte-identical output when applied twice. The
non-obvious part is where the section *ends* — and the classic mistake is
trusting a bare `^##` regex to find that boundary.

## When To Use

- An agent or script keeps a canonical section up to date in a document it
  does not fully own — `README.md`, `AGENTS.md`, `CLAUDE.md`, a handbook, a
  changelog preamble — on a "replace if present, append if missing" rule.
- You see any of these symptoms:
  - The maintained section is duplicated after every run.
  - A merge cut the section short in the middle of a fenced code block.
  - A `## ...` line *inside* a code fence was treated as the next section.
  - A `###` subheading ended the replace range early.
  - The updater never converges: the second run still changes the file.
  - After a merge, everything below some point renders as one giant code
    block (a leftover fence delimiter re-opened as a new fence).
- You are *reading* or *counting* sections with `^##`-style patterns, not
  only writing them — extraction and verification hit the same trap.

## The Trap: Headings Inside Code Fences

The naive implementation takes the replace range as "the `## X` line up to
the line before the next `^##` match". It breaks in two ways.

**Trap 1 — fenced literals.** Section bodies legitimately contain `## ...`
lines inside fenced code blocks: a report template, a sample document, a
quoted diff. A fence-blind scan misreads the first such line as the next
section boundary and cuts the range there:

````markdown
## Automation notes

The bot refreshes this section.

```text
## Weekly report        <- naive ^## scan stops here, inside the fence
- highlights:
- risks:
```

Keep the template fenced so it does not become a real heading.

## License              <- the real next section
````

Replacing up to the fenced line leaves a stale tail behind. The measured
corruption (see the tested trap fixture) is worse than a simple duplicate:

- The fenced `## Weekly report` literal is left behind *outside* any fence
  and renders as a real, duplicate-looking heading.
- The old block's closing fence delimiter (the bare `` ``` `` line) is also
  left behind, re-opens as a *new* fence, and swallows everything after
  it — the following `## License` section stops being a heading at all.
- The merge is not idempotent: every run matches the heading again, cuts at
  a fenced line again, and grows the file again.

**Trap 2 — `###` subheadings.** A bare `^##` regex also matches `###`,
`####`, and so on. If the section contains subheadings, the range ends at
the first `### ...` line and the old subsections survive after the new
ones — duplicated content below the merge point.

The same two traps apply to *reading* a section (extracting its content)
and to *verifying* one (`grep -c '^## X'` can both over-count fenced
literals and be fooled by them).

## Safe Boundaries: Two Methods

### Method 1 — literal-region-aware heading scan

Track literal-region state while scanning lines, and only treat a line as a
heading or boundary when it is outside frontmatter, fences, and supported raw
HTML blocks:

- A fence opens on a line whose first non-space characters (at most 3
  leading spaces) are a run of 3+ backticks or 3+ tildes.
- It closes only on a line with a run of the *same* character, *at least as
  long*, with nothing else on the line. An unclosed fence runs to the end
  of the document (CommonMark behaviour).
- While inside a fence, nothing is a heading and nothing is a boundary.
- Before fence scanning, a leading exact-line frontmatter block is excluded:
  YAML opens with `---` and closes with exact `---` or `...`; TOML opens and
  closes with exact `+++`. A heading-looking line inside that block is
  metadata/comment content, not a Markdown section boundary. An opener without
  its exact closer fails closed before any write.
- At top level, column 0–3
  [CommonMark 0.31.2 raw HTML blocks](https://spec.commonmark.org/0.31.2/#html-blocks)
  of types 1–7 are excluded too: literal-content tags
  (`pre`/`script`/`style`/`textarea`), comments, processing instructions,
  declarations, CDATA, listed block tags, and complete tag-only lines. Types
  6/7 end before a blank line or at EOF; type 7 cannot interrupt a paragraph.
  Tag and attribute grammar stays ASCII-only even under case-insensitive
  matching; Unicode case-fold lookalikes are ordinary Markdown.
  Fence and HTML state share one exclusive scanner, so delimiters inside the
  other region stay literal data.
- This mutating reference is intentionally stricter than CommonMark at an
  ambiguous EOF: a recognized type 1–5 opener without its explicit end
  condition fails closed. A possible type 7 opener after an unsupported
  container/indented context also fails closed instead of guessing. The same
  rule covers a possible link reference definition followed by `===`, where
  a partial parser cannot prove whether setext ended the paragraph. Possible
  link labels are tracked across lines with escape-aware bracket handling; an
  unescaped `]` without an immediate colon releases the possible-definition
  state. The simple no-blank definition-plus-tag form remains inline HTML.
- The section boundary is the next heading of level 1 **or** 2. The folk
  form is `^##[^#]` (exclude `###`); the reference hardens it to
  `^ {0,3}#{1,2}([ \t]|$)`, which additionally treats an H1 as a boundary
  (a part boundary must never be swallowed into a replace), accepts the
  1–3 space indent CommonMark allows, and applies CommonMark's space rule
  so a `##hashtag`-style paragraph line is not a false boundary. `###` is
  still not a boundary either way.

Use this method when the document format is not yours to change, or when
the tool must work on arbitrary Markdown.

### Method 2 — fixed markers

Wrap the maintained section in unique sentinel lines — HTML comments are
invisible in rendered Markdown:

```markdown
<!-- managed-section:begin automation-notes -->
## Automation notes

...body, fenced templates welcome...
<!-- managed-section:end automation-notes -->
```

Replace everything between the exact begin/end marker lines; append the
whole marked block when the begin marker is absent. Heading scanning is no
longer part of range-finding at all.

Use this method when you own the document format: it is the most robust
boundary (it survives heading renames and any body content), at the cost of
planting markers. Keep marker strings unique per section, and keep the
single-H2 invariant below even here — markers protect the *range*, not the
document's heading structure. One honesty note: a literal copy of the
marker line inside a code fence would still confuse a fence-blind marker
search, so either keep marker text out of fenced examples or find markers
with the same fence-aware scan.

## Invariants

Enforce these before and after every merge; they make "replace or append"
well defined:

1. **The block starts with its own plain `## Heading` line** — use exactly one
   ASCII space between the opening `##` and nonempty content (or exact `##` for
   an empty H2). Extra separator spaces/tabs and a CommonMark closing-hash
   sequence are not canonical here. The heading is part of the merged content,
   not configuration that can drift from it.
2. **Exactly one H1/H2-level heading inside the block** — its own first
   ATX line (literal-region-aware count). A block with a second ATX or setext
   H1/H2 would silently absorb or divide a section on a later run. Reject
   possible setext headings before the first write so append and replace use
   the same validation boundary. Fenced or raw-HTML heading literals inside
   the block are fine — they do not count.
3. **At most one unambiguous copy of the heading in the target** (outside
   literal regions). If the document already contains duplicates or a
   separator / 1–3-space / closing-hash alias of the managed H2, stop and
   report instead of "fixing" one form and leaving another.
4. **A canonical separator shape.** Store the block with no trailing blank
   lines; write exactly one blank line between the block and a following
   section. Reading range and writing shape then converge to the same
   bytes, which is what makes the second run a no-op.
5. **No unclosed fences, in the target or in the block.** CommonMark runs an
   unclosed fence to the end of the document, so in a target it silently
   extends the section to EOF (a replace would rewrite the whole visually
   swallowed tail), and in a block it would swallow whatever follows the
   merged section. Both are malformed input: stop and report.
6. **No setext headings inside the canonical block or replaced span.**
   A `===` or `---` underline directly under paragraph text is a real
   heading that a line-by-line `^#` scan cannot see as a boundary. Accepting
   one in a newly appended block would make the next run fail instead of
   converge; replacing across one would delete a section boundary. Stop
   before writing in both cases (convert the heading to ATX form, or use
   fixed markers). See
   [`docs/setext-block-heading-contract.md`](docs/setext-block-heading-contract.md).
7. **No unclosed leading frontmatter.** When the first line is exact `---`
   (YAML) or `+++` (TOML), ignore its heading-looking lines only through the
   first exact matching closer. If no closer exists, stop instead of guessing
   whether the opener was metadata or an ordinary Markdown thematic break.
8. **No ambiguous explicit-end raw HTML.** CommonMark lets types 1–5 run to
   EOF when their end condition is missing, but appending an H2 there would
   hide it inside raw HTML and break convergence. Require the explicit end
   condition in both target and block. For type 7, preserve its
   no-paragraph-interruption rule and fail closed when an unsupported
   container/indented or possible-reference-plus-setext context, including a
   multi-line link label, makes that decision ambiguous.
9. **ASCII-only block whitespace.** Blank lines, ATX heading trim and closing
   sequences, setext context, and fence closers use ASCII space/tab exactly as
   CommonMark 0.31.2 specifies. NBSP, EM SPACE, form feed, vertical tab, and
   other Unicode whitespace remain content instead of being silently stripped.
   See
   [`docs/commonmark-ascii-whitespace-contract.md`](docs/commonmark-ascii-whitespace-contract.md).

## Verification Recipe

Run all three checks after a merge; together they catch boundary bugs,
duplicate sections, and collateral edits:

1. **apply-twice-diff-zero.** Apply the same merge twice; after the second
   run `git diff` must be empty (or compare bytes before/after). For a
   section that sits at the end of the file, an equivalent check is that
   the block equals the file's entire tail section.

   ```bash
   python scripts/merge_section.py target.md section.md
   python scripts/merge_section.py target.md section.md   # second run
   git diff --exit-code target.md   # after committing the first run: empty
   ```

   The reference implementation's `--check` mode encodes the same idea with
   diff-style exit codes (0 = canonical, 1 = a merge would change the
   file), which CI can call directly.

2. **Heading occurrence count = 1.** Count lines equal to the heading with
   a fence-aware scan. A bare `grep -c '^## Automation notes$'` is subject
   to the very trap this skill is about — acceptable only when you know the
   body contains no fenced copy of the exact heading line.

3. **`git diff --stat` touches exactly one file** — the target. Anything
   else means the automation wrote where it should not have.

## Reference Implementation

[`scripts/merge_section.py`](scripts/merge_section.py) implements Method 1
plus every invariant above in dependency-free Python 3 (standard library
only). Python was chosen over a shell for one reason that matters here:
explicit byte-level control of newlines and BOM makes apply-twice-diff-zero
provable, where shell text pipelines tend to normalize line endings behind
your back. The algorithm ports directly to any language.

```bash
python scripts/merge_section.py TARGET.md SECTION.md            # merge in place
python scripts/merge_section.py TARGET.md SECTION.md --check   # drift check
```

`SECTION.md` is the canonical block: first line is exact `## Heading`, with one
ASCII space before nonempty content, or exact `##` for an empty H2.
The target's LF/CRLF style and UTF-8 BOM are preserved; a missing target is
created (append into an empty document). Changed bytes are flushed to an
exclusive same-directory temporary file and installed with one atomic replace.
The temporary starts as POSIX mode `0600` or a protected Windows
SYSTEM/Owner-Rights-only DACL, and its identity, bytes, metadata, and Windows
DACL are rechecked before commit. Existing POSIX owner/group/mode/bounded
extended attributes are preserved. For an existing Windows target,
`ReplaceFileW` carries forward its documented DACL, file attributes, and named
streams; exact owner/group/SACL preservation is not promised. Missing targets
retain the private permissions.
Both target and canonical block are read as no-follow ordinary-file snapshots.
Symbolic-link, non-regular, and multi-hard-link inputs are refused because
replacement would change their semantics; Windows reparse points and
EFS-encrypted Windows targets are refused before content is read.
The target identity, metadata, and bytes are rechecked just before commit.
Changes completed before that check are detected, but the check and replacement
are separate operations; externally serialize every writer when an existing
target must not suffer a lost update. Missing-target creation uses a no-replace
commit and never overwrites a concurrent creation. On Windows, `ReplaceFileW`
uses a private recovery backup; ambiguous partial failures raise
`AtomicCommitError` and retain recovery artifacts instead of guessing.
`AtomicCommitError.committed` is `True`, `False`, or `None` when the observed
state cannot prove either outcome. Cleanup checks identity immediately before
unlink, but portable path-based unlink still has a final name-swap window;
private unpredictable names and fail-closed artifact retention mitigate it.
Malformed input — a duplicate or syntactically aliased managed heading in the
target, a noncanonical separator, closing-hash, or extra heading in the block,
an unclosed fence or
explicit-end raw HTML block in either, ambiguous type 7 context, unclosed
leading YAML/TOML frontmatter, CR-only line endings, or a possible setext
heading inside the block or replaced span — exits with code 2 instead of
guessing. Exact leading
YAML (`---` through `---` or `...`) and TOML (`+++` through `+++`) frontmatter
plus the supported raw HTML profile are excluded from heading scans, so
literal headings cannot become replacement anchors.
All block-grammar trimming is limited to ASCII space/tab; Unicode whitespace
bytes remain content and cannot be normalized into a heading, blank line,
closing-hash sequence, or fence closer.
When only mixed line endings need normalizing, the action is reported honestly
as `normalized` / `would-normalize`, never as "unchanged".

The fixtures under [`tests/fixtures/`](tests/fixtures) are one folder per
case, each with `input.md`, `section.md`, and `expected.md`:

| Fixture | Proves |
| --- | --- |
| `trap-heading-inside-fence` | Fenced `## ...` literals do not end the range |
| `frontmatter-heading-literal` | Leading YAML metadata comments are not merge anchors |
| `html-block-heading-literal` | Raw HTML `#` / `##` literals do not cut the range |
| `append-missing-section` | Absent section is appended with one separator line |
| `replace-existing-section` | Present section is replaced in place |
| `subheading-boundary` | `###` subheadings stay inside; range ends at the next real `##` |
| `h1-boundary` | An `#` part heading ends the range instead of being swallowed |

The self-test runs with no dependencies and is part of CI:

```bash
python scripts/test_merge_section.py
```

Besides the contract tests (expected output, apply-twice-diff-zero,
heading count = 1, CRLF/BOM byte stability), the suite keeps the trap
*measured*: it contains the fence-blind implementation as
`fence_blind_merge` and asserts that on the trap fixture it corrupts the
document (the following section is swallowed by a re-opened fence, the
fenced literal escapes and renders as a heading), that it is not idempotent
(the second application changes the file again), and that it cuts the range
at a `###` subheading on the subheading fixture. If someone "simplifies"
the scanner back into the trap, these tests fail first.

## Limitations

- The canonical heading itself must be a plain `## Name` at column 0 with one
  ASCII separator space before nonempty content; exact `##` is the empty H2.
  Extra block separator spaces/tabs are rejected, and a matching target
  separator alias makes merge and `--check` refuse without writing. See
  [`docs/managed-heading-separator-contract.md`](docs/managed-heading-separator-contract.md).
- A closing-hash block heading is rejected. A matching closing-hash form
  (`## X ##`) outside literal regions is an ambiguous managed-heading
  identity, so merge and `--check` refuse without writing. The reference does
  not rewrite or deduplicate it automatically. See
  [`docs/closing-hash-managed-heading-contract.md`](docs/closing-hash-managed-heading-contract.md).
- A matching H2 indented by 1–3 ASCII spaces outside literal regions is an
  ambiguous managed-heading identity. Merge and `--check` both refuse it
  without writing; the reference never auto-reindents possible list/container
  content. See
  [`docs/indented-managed-heading-contract.md`](docs/indented-managed-heading-contract.md).
- Setext headings (`Heading` + `===`/`---` underline) are never boundaries;
  when one may sit inside the canonical block or replaced span the reference
  refuses before writing (invariant 6). This avoids first-append/next-run
  divergence and silent boundary deletion. Convert setext headings to ATX
  form, or switch to fixed markers. See
  [`docs/setext-block-heading-contract.md`](docs/setext-block-heading-contract.md).
- Only a document-leading, column-0, exact-line frontmatter form is recognized:
  YAML `---` with `---`/`...`, or TOML `+++` with `+++`. Delimiters with
  trailing text/space, frontmatter later in a document, and other metadata
  syntaxes are ordinary Markdown to this reference. A recognized opener with
  no exact closer is refused (invariant 7).
- Fence handling covers column 0–3 backtick/tilde fences per CommonMark's
  core rules, including the backtick-info-string exclusion; exotic cases
  (fences inside blockquotes or deep list indentation) are out of scope
  for the reference.
- Raw HTML handling covers top-level, column 0–3 CommonMark 0.31.2 block
  types 1–7. Container prefixes, lazy continuations, and 4+ character
  indentation are not fully parsed. A possible type 7 opener after such an
  unknown context is refused; add a blank line or use fixed markers.
  Unlike CommonMark, recognized type 1–5 blocks require their explicit end
  condition for mutation safety. See
  [`docs/html-block-heading-scan-contract.md`](docs/html-block-heading-scan-contract.md).
- UTF-8 documents with LF or CRLF line endings only. CR-only (classic Mac)
  endings are refused — they would break byte idempotency. A file mixing
  CRLF and LF is treated as CRLF (any CRLF present selects CRLF), rewritten
  once as `normalized`, and stable from the second run on.
- The target must be missing or an ordinary file with one hard link.
  Symbolic-link and multi-hard-link targets are refused; choose the intended
  ordinary file path explicitly.
- Existing Windows targets must be owned by the effective token's default
  owner. Do not use the reference implementation when exact owner/group/SACL
  preservation is required; EFS-encrypted targets are refused because the
  same-directory temporary is plaintext.
- One file, one section per invocation — by design, so that verification
  stays sharp (`git diff --stat` = exactly one file).

## Provenance

Distilled from real agent operations that maintain canonical sections in
agent-instruction Markdown (README / AGENTS.md-style documents), where a
maintained section legitimately embeds `## ...` report templates inside
fenced code blocks. The corruption described in Trap 1 was hit in practice
by a fence-blind merge; the fixtures and the `fence_blind_merge` tests in
this repository reproduce it so the failure mode stays measured instead of
anecdotal. This document itself — fenced examples containing `## ...`
lines — is exactly the kind of file that breaks naive section tooling.
