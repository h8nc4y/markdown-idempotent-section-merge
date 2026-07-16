# Contributing

Thanks for improving this skill. This repository is intentionally small:
changes should make the section-merge discipline safer, clearer, or easier
to verify.

## Before You Start

- Read [SKILL.md](SKILL.md) and the examples under [examples](examples).
- `SKILL.md` (English) is canonical. When you change it, update
  [docs/SKILL.ja.md](docs/SKILL.ja.md) in the same pull request so the two
  stay in sync.
- Do not paste tokens, credentials, private keys, OAuth codes, raw logs,
  customer data, private repository names, or internal absolute paths into
  issues, pull requests, commits, examples, or fixtures. No token or secret
  value ever belongs in this repository.
- Use synthetic placeholders such as `TARGET.md`, `SECTION.md`, and
  `## Automation notes` for examples.
- Put personal or organization-specific scan markers in an untracked
  `.private-markers.local` file, not in repository source.

## Grounding Rules

This skill's value is that the failure mode stays measured, not
hypothetical. Keep it that way:

- The `TrapProofTests` in `scripts/test_merge_section.py` deliberately
  contain the broken fence-blind implementation and assert that it fails.
  Do not delete or weaken them; they are the recorded measurement.
- If you change merge behaviour, update the fixtures, the tests, and
  `SKILL.md` together — a behaviour change that only edits prose is not
  complete.
- Claims about Markdown or renderer behaviour should be grounded in
  something observable (a fixture, a reproducible command). Mark
  design-derived-but-unvalidated guidance explicitly as unverified.

## Development Workflow

1. Create a focused branch.
2. Make the smallest coherent change.
3. Update examples or README text when user-facing guidance changes.
4. Add or adjust fixtures and tests when merge behaviour changes.
5. Run the validation commands before opening a pull request.

## Validation

From the repository root, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-oss-readiness.ps1
python scripts\test_merge_section.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-scan-private-markers.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\scan-private-markers.ps1
git diff --check
```

If `pwsh` is available, it is also acceptable for the PowerShell scripts:

```powershell
pwsh -NoProfile -File .\scripts\validate-oss-readiness.ps1
pwsh -NoProfile -File .\scripts\test-scan-private-markers.ps1
pwsh -NoProfile -File .\scripts\scan-private-markers.ps1
```

On macOS, Linux, or any POSIX shell with PowerShell 7 (`pwsh`) installed,
use forward slashes and `python3`:

```bash
pwsh -NoProfile -File ./scripts/validate-oss-readiness.ps1
python3 scripts/test_merge_section.py
pwsh -NoProfile -File ./scripts/test-scan-private-markers.ps1
pwsh -NoProfile -File ./scripts/scan-private-markers.ps1
```

## Pull Request Expectations

- Explain the problem and the chosen fix.
- Include validation results.
- Call out any remaining unknowns.
- If the change alters boundary detection, an invariant, or the
  verification recipe, describe the failure mode it prevents (or the false
  refusal it removes) concretely — ideally as a new fixture.

## Maintainer Notes

Prefer documentation, fixtures, and validation that prevent silent content
loss. Avoid adding broad dependencies or network-backed checks unless they
are clearly necessary for public safety; the reference implementation and
its tests stay standard-library only.
