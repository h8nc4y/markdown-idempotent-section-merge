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

### Method 1 — fence-aware heading scan

Track fence state while scanning lines, and only treat a line as a heading
or boundary when it is outside every fence:

- A fence opens on a line whose first non-space characters (at most 3
  leading spaces) are a run of 3+ backticks or 3+ tildes.
- It closes only on a line with a run of the *same* character, *at least as
  long*, with nothing else on the line. An unclosed fence runs to the end
  of the document (CommonMark behaviour).
- While inside a fence, nothing is a heading and nothing is a boundary.
- The section boundary is the next H2-level heading only: `^##[^#]` in grep
  terms (`###` is not a boundary). The reference implementation writes it
  as `^##(?!#)` so a bare `##` line also counts.

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

1. **The block starts with its own `## Heading` line** — the heading is
   part of the merged content, not configuration that can drift from it.
2. **Exactly one H2-level heading inside the block** (fence-aware count).
   A block with a second H2 would silently absorb the next section on the
   following run. Fenced `## ...` literals inside the block are fine — they
   do not count.
3. **At most one copy of the heading in the target** (outside fences).
   If the document already contains duplicates, stop and report instead of
   "fixing" one of them and leaving the other.
4. **A canonical separator shape.** Store the block with no trailing blank
   lines; write exactly one blank line between the block and a following
   section. Reading range and writing shape then converge to the same
   bytes, which is what makes the second run a no-op.

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

`SECTION.md` is the canonical block: first line is the exact `## Heading`.
The target's LF/CRLF style and UTF-8 BOM are preserved; a missing target is
created (append into an empty document).

The fixtures under [`tests/fixtures/`](tests/fixtures) are one folder per
case, each with `input.md`, `section.md`, and `expected.md`:

| Fixture | Proves |
| --- | --- |
| `trap-heading-inside-fence` | Fenced `## ...` literals do not end the range |
| `append-missing-section` | Absent section is appended with one separator line |
| `replace-existing-section` | Present section is replaced in place |
| `subheading-boundary` | `###` subheadings stay inside; range ends at the next real `##` |

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

- ATX headings at column 0 only. Setext headings (`Heading` +
  `===`/`---` underline), closing-hash form (`## X ##`), and 1–3 space
  indented headings are treated as body text. Keep canonical section
  headings in plain `## Name` form at column 0.
- Fence handling covers column 0–3 backtick/tilde fences per CommonMark's
  core rules; exotic cases (fences inside blockquotes or deep list
  indentation) are out of scope for the reference.
- UTF-8 documents only. Mixed line endings are normalized to the detected
  dominant style on the first write and are stable from the second run on.
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
