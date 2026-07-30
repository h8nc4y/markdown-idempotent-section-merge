# HANDOFF

## Current state

進行中: INPUT-BUDGET-01（Class M）で、Markdown mergeのraw input readを
target 8 MiB・canonical block 2 MiBへ固定した。topic branch
`fix/bound-merge-input-bytes`で実装・文書同期・local full suiteまで完了。
commit、push、PR、3 OS CI、main統合は未確認。

## Objective / impact

- hostile・誤投入の巨大Markdownを無制限に`read()`せず、BOM・改行を含むraw
  bytes段階でfail closedにする。
- target初回、block初回、commit直前target再読を独立budgetへ通す。
- over-limit拒否ではexit 2、固定・path-free診断、no-write、private temp 0を保つ。

## Delivered

- 共通snapshot helperの`max_bytes` / `oversize_error`を必須keyword化し、
  全readを`limit + 1`へ固定。無制限defaultは残していない。
- target 8 MiB、block 2 MiB。exact limitは成功し、limit+1は書込み前に拒否する。
- replacement/recovery snapshotは`len(expected_bytes)`を上限にし、Windows
  helperのunfingerprinted fallbackを削除した。
- commit前はappeared/disappeared/metadata driftをsize診断より優先し、
  snapshot前後の二重guardを維持する。
- README / SECURITY / SKILL / 日本語SKILL / CHANGELOGを同期した。

## Verification

- 変更前baseline: 153 tests、`OK (skipped=14)`。
- RED: 新規4 testsが定数不存在で4 errorになることを確認。
- focused input/temporary/symlink: 7 tests、`OK (skipped=2)`。
- focused + Windows state-machine: 25 tests、`OK (skipped=1)`。
- 変更後full suite: 159 tests、`OK (skipped=15)`。
- PowerShell 7 / Windows PowerShell 5.1 readiness: PASS。
- 両PowerShellのprivate-marker scanner self-test / actual scan: PASS。
- Gitleaks: history 20 commits・worktreeともfinding 0。
- Semgrep `p/default`: 324 rules / 34 files / finding 0。
- 変更8ファイル: strict UTF-8、LF、BOM/NULなし。compileall、CLI help、
  `git diff --check`: PASS。
- 独立source review / tests-docs review: P0〜P3 CLEAR。

## Decisions / residual risks

- 8/2 MiBは互換性より安全性を優先したraw I/O境界。blockは1節だけなのでtargetと
  同額にしない。
- byte budgetはpeak-memory保証ではない。decode、line/state list、merge output、
  CRLF正規化で増幅する。line-count budget / streaming parserは後続候補。
- commit再確認とreplaceはCASではなく、既存の最終lost-update windowは残る。
- Windows native以外と3 OS CIは未確認。Release / tagはowner gateのまま。

## Key files / next steps

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `README.md`
- `SECURITY.md`
- `SKILL.md`
- `docs/SKILL.ja.md`
- `CHANGELOG.md`

1. final docs差分のreadiness・actual scan・Gitleaks worktreeを再確認する。
2. commit → push → PR → 3 OS CI → merge → post-main再検証・cleanupを行う。
