# Security Policy

This repository documents a Markdown section-merge discipline and ships a
reference implementation that atomically replaces ordinary target files. It
should never contain secrets, but its guidance drives automation that edits user
documents, so unsafe guidance is treated as a security problem too.

## Supported Versions

The `main` branch is the supported version. Tagged releases receive fixes
through new tags on `main`.

## Reporting A Vulnerability

Use GitHub private vulnerability reporting for:

- A real secret, credential, or private identifier accidentally committed
  to this repository.
- Guidance or reference-implementation behaviour that could silently
  destroy user content (for example a boundary rule that swallows a
  neighbouring section without an error), leak private data, or write
  outside the target file.
- A write-path issue that can truncate the original before commit, leave
  attacker-controlled temporary files, widen existing permissions, or follow
  a linked target unexpectedly. The reference implementation uses an
  exclusive same-directory temporary file and flushes it before final commit,
  uses Windows
  [`ReplaceFileW`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)
  with a private recovery backup to preserve ACLs/attributes, preserves bounded
  POSIX owner/group/mode/extended attributes, and refuses symbolic-link,
  Windows reparse-point, non-regular, or multi-hard-link targets before
  reading. Recoverable Windows failures restore the verified original;
  ambiguous partial states retain named recovery artifacts and raise
  `AtomicCommitError` instead of deleting evidence.
  Identity, metadata, and bytes are rechecked before commit, but this is a
  best-effort check rather than compare-and-swap. Serialize all writers
  externally when lost-update prevention for an existing target is required.
  Missing-target creation uses a no-replace commit.
- A validation gap that allows unsafe public examples.

Do not open a public issue containing tokens, credentials, private keys,
OAuth material, customer data, raw secret-bearing logs, or private
repository names and internal paths.

## Public Issue Safety

Public issues may include:

- Symptom class, such as "section duplicated on every run" or "range cut
  inside a code fence".
- Sanitized reproductions built from synthetic Markdown (the fixtures are a
  good template).
- Exit codes and redacted output of the reference implementation.

Public issues must not include:

- Secret values or secret-display command output.
- Private repository names, internal absolute paths, hostnames, or
  customer data.
- Raw agent transcripts or real maintained documents that contain any of
  the above.

## Scanner Coverage

The private-marker scanner (`scripts/scan-private-markers.ps1`) is a
best-effort safety net, not a guarantee. For git-tracked text paths, it
scans both the exact index blob and a distinct current regular worktree
snapshot. It reads intent-to-add from the index extended flags and
rechecks raw stage/debug metadata immediately before reporting. It does
not follow worktree links or fetch missing Git objects; ambiguous index,
root, link, encoding, process, drift, count, or size states fail closed.
File, entry, line, regex-match, finding, byte, process, output, and time
budgets bound hostile input.
If Git cannot establish a valid work tree, a root-level `.git` file or
directory (or equivalent metadata on an ancestor) fails closed. Only nested
`.git` directories and leaf `.git` files inside that non-Git scan root are
treated as Git control metadata: the scanner excludes them and never follows
their contents or external targets.

The scanner checks a curated set of secret prefixes (GitHub, OpenAI, AWS,
GCP, Slack, Stripe, PEM key blocks, and similar), private-looking absolute
Windows paths, non-allowlisted GitHub repository URLs, and configured
local markers, and it redacts any matched value.
`.private-markers.local` must remain untracked. The scanner does not
detect every possible secret format and is no substitute for keeping real
credentials out of the repository in the first place. Treat a passing
scan as "no known marker found," not "definitely safe."

## Response Expectations

Maintainers should acknowledge actionable security reports when available,
remove or redact unsafe public material, and prefer guidance that reduces
data-exposure and content-destruction risk. If real exposure is possible,
rotate the affected secret outside this public repository and document only
the remediation status.
