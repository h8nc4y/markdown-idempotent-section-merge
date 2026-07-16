# Verification Recipe (One Page)

Run all three checks after every automated section merge. Together they
catch boundary bugs, duplicate sections, and collateral edits. `TARGET.md`
is the document being maintained; `SECTION.md` is the canonical block whose
first line is the exact `## Heading`.

## 1. apply-twice-diff-zero

The same merge applied twice must leave the file byte-identical.

POSIX shells:

```bash
python3 scripts/merge_section.py TARGET.md SECTION.md
cp TARGET.md /tmp/after-first-run.md
python3 scripts/merge_section.py TARGET.md SECTION.md
cmp TARGET.md /tmp/after-first-run.md && echo "idempotent"
```

PowerShell:

```powershell
python scripts\merge_section.py TARGET.md SECTION.md
Copy-Item TARGET.md "$env:TEMP\after-first-run.md"
python scripts\merge_section.py TARGET.md SECTION.md
if ((Get-FileHash TARGET.md).Hash -eq (Get-FileHash "$env:TEMP\after-first-run.md").Hash) {
  'idempotent'
}
```

In a git working tree, the equivalent habit is: commit (or stage) the first
run, apply the merge again, and require `git diff --exit-code TARGET.md` to
pass.

For a section that sits at the end of the file there is an equivalent
manual check: the canonical block must equal the file's entire tail
section, from its heading to EOF.

## 2. Heading occurrence count = 1

Count with a fence-aware scan — a bare grep is subject to the very trap
this skill documents:

```bash
python3 - TARGET.md '## Automation notes' <<'PY'
import sys
sys.path.insert(0, "scripts")
import merge_section
lines = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n").split("\n")
count = len(merge_section.heading_occurrences(lines, sys.argv[2]))
print("headings:", count)
sys.exit(0 if count == 1 else 1)
PY
```

`grep -c '^## Automation notes$' TARGET.md` is acceptable only when you
know the document contains no fenced copy of the exact heading line.

## 3. `git diff --stat` touches exactly one file

```bash
git diff --stat
```

Anything beyond the one target file means the automation wrote where it
should not have. Investigate before committing.

## CI drift check

The reference implementation's `--check` mode uses diff-style exit codes,
so CI can require that a maintained section is already canonical:

```bash
python3 scripts/merge_section.py TARGET.md SECTION.md --check
# exit 0: up-to-date
# exit 1: a merge (or line-ending normalization) would change the file
# exit 2: invariant violation (bad block, duplicate headings, unclosed
#         fence, CR-only endings, possible setext heading) or usage error
```
