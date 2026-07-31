# HANDOFF

## Current state

進行中: OUTPUT-CLOSURE-01（Class M）。final raw outputをtarget inputと同じ
8 MiBへ閉じる実装・local gate・独立reviewは完了。PR / 3 OS CI待ち。

完了: INPUT-BUDGET-01（Class M）はPR #24（merge commit `d263c58`）で
`main`へ統合した。統合後のWindows CIで検出したpath `lstat` / handle
`fstat`間のtimestamp精度差とfixture provenance不一致は、follow-up PR #25
（merge commit `6df6872`）で修正した。

PR #25のexact head `8772b44`はUbuntu / Windows / macOS 15 CIが全PASS。
`main`を`6df6872`へfast-forwardしたpost-main再検証もPASSしている。
INPUT-BUDGET-01の実装・follow-up用worktreeとlocal / remote branchはcleanup済み。

## Objective / impact

- hostile・誤投入の巨大Markdownを無制限に`read()`せず、BOM・改行を含むraw
  bytes段階でfail closedにする。
- target初回、block初回、commit直前target再読を独立budgetへ通す。
- over-limit拒否ではexit 2、固定・path-free診断、no-write、private temp 0を保つ。
- BOM・LF/CRLF復元後のfinal raw outputを8 MiB以下にし、exact-limit出力の
  次回no-opとlimit+1拒否を保証する。

## Delivered

- 共通snapshot helperの`max_bytes` / `oversize_error`を必須keyword化し、
  全readを`limit + 1`へ固定。無制限defaultは残していない。
- target 8 MiB、block 2 MiB。exact limitは成功し、limit+1は書込み前に拒否する。
- final outputもtargetと同じ8 MiB。BOM/EOL復元後のraw bytesをtemp作成前に検査し、
  limit+1を通常実行・`--check`とも固定診断で拒否する。
- managed sectionが同一でもmixed EOL正規化だけでlimit+1になる経路を、通常実行・
  `--check`の両方でno-write検証した。
- replacement/recovery snapshotは`len(expected_bytes)`を上限にし、Windows
  helperのunfingerprinted fallbackを削除した。
- commit前はappeared/disappeared/metadata driftをsize診断より優先し、
  snapshot前後の二重guardを維持する。
- preliminary path guardはstable identityだけを比較し、Windowsのpath / handle間の
  timestamp精度差を許容する。最終descriptor snapshotのfull fingerprint比較は
  strictのまま維持する。
- Windows state-machine fixtureはprotected temporaryのexpected statをwrite handleの
  `os.fstat()`から取得し、production call contractと一致させた。
- README / SECURITY / SKILL / 日本語SKILL / CHANGELOGを同期した。

## Verification

- OUTPUT-CLOSURE-01 baseline: 160 tests、`OK (skipped=15)`。
- RED: 新規3 testsがoutput定数不存在で4 errorになることを確認。
- focused output closure: 4 tests、`OK`。実8 MiB exact appendと次回no-op、
  append / BOM+CRLF longer replacement / mixed-EOL normalizationのlimit+1
  no-writeを確認。
- 変更後full suite: 164 tests、`OK (skipped=15)`。
- PowerShell 7 / Windows PowerShell 5.1 readiness: PASS。
- 両PowerShellのprivate-marker scanner self-test / actual scan: PASS。
- Gitleaks: history 31 commits・worktreeともfinding 0。
- Semgrep: global hook rule setによるfull worktree scan、finding 0。
- 変更9ファイル: strict UTF-8・LF・NULなし。PowerShell readinessのみ既存の
  UTF-8 BOMを保持。compileall、CLI help、`git diff --check`: PASS。
- 独立source review / tests-docs review: mixed-EOL testとSECURITY readiness固定を
  追加後、P0〜P3 CLEAR。
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
- PR #24 CI: Ubuntu / macOS 15 PASS、Windows FAIL（3 failures / 1 error）。
  原因はpath / handle間のtimestamp精度差とfixture stat取得元の不一致。
- follow-up focused 25 tests: `OK (skipped=1)`。
- follow-up full suite 160 tests: `OK (skipped=15)`。
- python.org CPython 3.14.6 / Windowsの独立full suite: 160 tests、
  `OK (skipped=15)`。focused 4 tests × 20回もfailure 0。
- follow-up PowerShell 7 / Windows PowerShell 5.1 readiness・actual
  private-marker scan: PASS。
- follow-up Gitleaks: history 14 commits・worktreeともfinding 0。
- follow-up Semgrep `p/default`: 324 rules / 34 files / finding 0。
- follow-up変更4ファイル: strict UTF-8、LF、BOM/NULなし。compileall、
  CLI help、`git diff --check`: PASS。
- Windows follow-up独立review: P0〜P3 CLEAR。
- PR #25 exact head `8772b44`: Ubuntu / Windows / macOS 15 CIが全PASS。
- merge commit `6df6872` post-main: full suite 160 tests、
  `OK (skipped=15)`。PowerShell 7 / Windows PowerShell 5.1 readiness・
  actual private-marker scan、Gitleaks worktree、compileall、CLI help、
  `git diff --check`: PASS。

## Decisions / residual risks

- 8/2 MiBは互換性より安全性を優先したraw I/O境界。blockは1節だけなのでtargetと
  同額にしない。
- byte/output budgetはpeak-memory保証ではない。decode、line/state list、output構築、
  CRLF正規化で増幅する。line-count budget / streaming parserは後続候補。
- commit再確認とreplaceはCASではなく、既存の最終lost-update windowは残る。
- Release / tagはowner gateのまま。

## Key files / next steps

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `README.md`
- `SECURITY.md`
- `SKILL.md`
- `docs/SKILL.ja.md`
- `CHANGELOG.md`

1. OUTPUT-CLOSURE-01をcommit / pushし、PR / 3 OS CI / merge / post-main確認を
   完了する。その後にpeak-memory fixtureを実測し、line-count budgetまたは
   streaming parserの互換性境界を決める。
