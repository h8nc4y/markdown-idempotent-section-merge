# HANDOFF

## Current state

INPUT-BUDGET-01（Class M）はPR #24で`main`へ統合済み
（merge commit `d263c58`）。統合後CIはUbuntu / macOS 15がPASSし、
Windowsがpath `lstat`とhandle `fstat`のtimestamp精度差、および
Windows fixtureのexpected stat取得元の不一致でFAILした。

現在はfollow-up branch `fix/windows-byte-budget-ci`で、preliminary path guardを
identity-only、最終descriptor fingerprintをstrictのままにし、Windows fixtureを
production同様のhandle由来statへ修正済み。local full suiteまでPASSしている。
follow-upのcommit、push、PR、3 OS CI、`main`統合は未確認。

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
- preliminary path guardはstable identityだけを比較し、Windowsのpath / handle間の
  timestamp精度差を許容する。最終descriptor snapshotのfull fingerprint比較は
  strictのまま維持する。
- Windows state-machine fixtureはprotected temporaryのexpected statをwrite handleの
  `os.fstat()`から取得し、production call contractと一致させた。
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

## Decisions / residual risks

- 8/2 MiBは互換性より安全性を優先したraw I/O境界。blockは1節だけなのでtargetと
  同額にしない。
- byte budgetはpeak-memory保証ではない。decode、line/state list、merge output、
  CRLF正規化で増幅する。line-count budget / streaming parserは後続候補。
- commit再確認とreplaceはCASではなく、既存の最終lost-update windowは残る。
- follow-up fixのUbuntu / Windows / macOS 15 CIは未確認。Release / tagはowner
  gateのまま。

## Key files / next steps

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `README.md`
- `SECURITY.md`
- `SKILL.md`
- `docs/SKILL.ja.md`
- `CHANGELOG.md`

1. follow-upをcommit → push → PRの順で進め、Ubuntu / Windows / macOS 15の
   全CI PASSを
   確認してからmergeする。
2. post-main再検証・正本closeout・worktree / branch cleanupを行う。
