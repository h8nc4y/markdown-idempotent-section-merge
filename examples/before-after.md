# Before / After: The Trap Fixture, Walked Through

This is the [`tests/fixtures/trap-heading-inside-fence`](../tests/fixtures/trap-heading-inside-fence)
case rendered side by side: what a fence-aware merge produces, and what the
fence-blind `^##` implementation produces on the same input. Everything
below is synthetic and reproduced by the test suite
(`python scripts/test_merge_section.py`), so the corruption stays measured,
not anecdotal.

## Input

The maintained section legitimately embeds a `## ...` line inside a fenced
code block — a report template:

````markdown
# Team handbook

Shared conventions for the docs team.

## Automation notes

The bot refreshes this section.

```text
## Weekly report
- highlights:
- risks:
```

Keep the template fenced so it does not become a real heading.

## License

MIT for all handbook content.
````

The block to merge (`section.md`) is the same section with an updated body
(one more template line, "every night" wording).

## After a fence-aware merge (correct)

The range runs from `## Automation notes` to the line before `## License` —
the fenced `## Weekly report` is body text, not a boundary:

````markdown
# Team handbook

Shared conventions for the docs team.

## Automation notes

The bot refreshes this section every night.

```text
## Weekly report
- highlights:
- risks:
- blockers:
```

Keep the template fenced so it does not become a real heading.

## License

MIT for all handbook content.
````

Applying the merge a second time changes nothing (apply-twice-diff-zero),
`## Automation notes` occurs exactly once, and `git diff --stat` touches
one file.

## After the fence-blind merge (measured corruption)

The naive scan stops at the first `^##` match after the heading — the
fenced `## Weekly report` line — and replaces only up to there, leaving the
old section's tail behind:

````markdown
# Team handbook

Shared conventions for the docs team.

## Automation notes

The bot refreshes this section every night.

```text
## Weekly report
- highlights:
- risks:
- blockers:
```

Keep the template fenced so it does not become a real heading.

## Weekly report
- highlights:
- risks:
```

Keep the template fenced so it does not become a real heading.

## License

MIT for all handbook content.
````

Three things went wrong, and the test suite asserts each one:

1. The old fenced `## Weekly report` literal is now *outside* any fence and
   renders as a real, duplicate-looking heading (fence-aware count went
   from 0 to 1).
2. The old block's closing fence delimiter survived as a stray bare
   ` ``` ` line, which *re-opens* as a new fence and swallows everything
   after it: `## License` stops being a heading at all (fence-aware count
   went from 1 to 0). Renderers show the tail of the document as one giant
   code block.
3. The merge is not idempotent. On the next run the heading matches again,
   the range is cut at a fenced line again, and the file grows again —
   the second application produces a different (larger) document than the
   first.

## Reproduce It

```bash
python scripts/test_merge_section.py
```

The `TrapProofTests` class contains the fence-blind implementation
(`fence_blind_merge`) and the assertions above; the `FixtureMergeTests`
class proves the fence-aware implementation handles the same input
correctly.
