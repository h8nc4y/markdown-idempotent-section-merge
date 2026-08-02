# HANDOFF

## Current goal

CHECKOUT-V7-01（Class M）のsource integration baselineと検証証跡を保持する。
LINE-BUDGET-01の統合済み状態とpolicy-rejected cleanup residueは変更せず、
Release / tagはowner gateのまま触れない。

## Current state

- CHECKOUT-V7-01のsource integration baselineはPR #32 merge
  `4715ebe749f978fb97c00daf636886b9ef7886e9`。
- PR #32 head `30041d573c822e7b48a72d94fb8f6cdcd65872b0`はmergeの祖先で、
  head / merge treeは`66bcdf8e3448a28240b4c5a6f7555839b4de513e`で一致する。
- checkout v5.1.0 / v7.0.1はいずれもNode.js 24 runtime。v7.0.1のverified tag commit
  `3d3c42e5aac5ba805825da76410c181273ba90b1`をfull SHAで固定した。
  実装契約は`docs/checkout-v7-upgrade.md`。feature branchはlocal / remoteからcleanup済み。
- LINE-BUDGET-01のsource integration baselineはPR #30 merge
  `fe36c0ca7094e034f12cae1449de82fc70fd194e`。
- PR #30 head `ec842253616374b7706061ccef517b37576968f3`はmergeの祖先で、
  head / merge treeは`c7f363f65ae86c99fd585dade9efee671402c14e`で一致する。
- remote feature branchは不存在。local `fix/line-count-budget` branchと分離worktreeは
  tracked cleanのまま保持し、ignored `scripts/__pycache__/`も削除しない。
- generated cache cleanupはpolicy層で拒否された。再試行や代替経路を使わず、
  local branch / worktree / ignored residueを一体で保持する。
- 実装済み: raw LF byte上限はtarget 1,000,000、canonical block 250,000、final
  output 1,000,000。CRLFは1回、BOMは0回と数える。
- exact limitは受理し、limit+1 inputはdecode前、outputはtemporary作成前に、
  固定・path-free診断、exit 2、no-writeで拒否する。
- peak計測CLIは現行newline budget内で全caseが動くようdefaultを1 MiBへ変更した。
  明示byte上限8 MiBは維持する。dense exact-8-MiB表はcommit `da8991d`時点の
  historical evidenceであり、現行codeでは意図どおりnewline budgetで拒否される。

## Success metrics

- target / block / finalのexactとlimit+1をLF・BOM+CRLFで境界検証する。
- normal / `--check`の双方でtarget/block超過は各入力のdecode前、final超過はmerge後・
  temporary作成前に拒否し、writeを許さない。
- 通常長の100,000行paragraphと既存byte / action / idempotency契約を維持する。
- docs、PowerShell 7 / 5.1 readiness、full suite、private-marker、Gitleaks、Semgrep、
  PR/CI/post-mainまで実測し、cleanup rejectionとresidue保持を正確に記録する。

## Evidence so far

- CHECKOUT-V7-01 baselineはPowerShell 7 / 5.1 readiness、Python 176 tests
  `OK (skipped=15)`、両runtimeのprivate-marker self-test / actual scanが成功した。
- validator-first TDDではworkflowを旧v5.1.0 pinのままにして、両PowerShellがexact v7 pin
  欠落とstructured workflow不一致の2 diagnosticsでREDになった。v7.0.1 full SHA更新後は
  mutable `@v7`、旧SHA、stale version comment mutationを含めて両方GREENへ戻った。
- candidateはPython 176 tests `OK (skipped=15)`。private-marker self-testは
  PowerShell 7で179.3秒、Windows PowerShell 5.1で124.4秒、actual scanは10.6秒 / 5.4秒で
  すべて成功した。Gitleaksはworktree finding 0、Semgrep
  `p/security-audit`は6 targets / 2 rulesでfinding 0。`actionlint`はhostに未導入のため
  未実行・未確認。
- 独立code reviewは、mutable / old-pin mutationがversion commentも同時に壊し、ref検査の
  退行をcomment違反だけで拒否できるfalse-greenをP2として検出した。正しいv7.0.1 commentを
  保ったままrefだけを壊すmutationへ分離し、両PowerShell readinessはGREEN。
- P2修復後の独立再review 2系統はP0〜P3すべて0。exact staged Gitleaksはfinding 0、
  Semgrep `p/security-audit`は6 targets / 2 rulesでfinding 0。
- PR #32 run `30749518917`とmerge commitのmain push run `30749727042`は、
  Windows / Ubuntu / macOS 15の3 jobが成功し、check annotationは各0。
- post-main local readinessとactual private-marker scanはPowerShell 7 / 5.1とも成功した。
  Python suiteとscanner self-testはpost-main localでは再実行せず、exact-main CIで成功した。
- TDD RED: 新規5 testsが未実装helper / constantsで13 errors。
- focused newline-budget 5 tests: `OK`。
- focused newline-budget + peak-default 6 tests: `OK`。
- 実装後full suite: 176 tests、`OK (skipped=15)`。
- current default peak matrix: 1 MiB、5 cases × 1 repetition。期待action、artifact 0。
- PowerShell 7 / Windows PowerShell 5.1 readiness: PASS。
- 両PowerShellのprivate-marker scanner self-test: PASS。
- 両PowerShellのactual private-marker scan: PASS。
- Gitleaks: worktree finding 0、history 34 commits finding 0。
- Semgrep `p/default`: 324 rules / 36 files / finding 0。
- LINE-BUDGET-01 PR #30: head `ec84225`、merge `fe36c0c`。PR run
  `30682947721`、post-main run `30683131893`はUbuntu / Windows / macOS 15でPASSし、
  各check annotationは0件。2026-08-01のcloseout着手時点でopen PR / issueは0件。
- 2026-08-01のcurrent main再確認: 176 tests `OK (skipped=15)`、Windows PowerShell
  readiness / private-marker self-test / actual scan / `git diff --check`はPASS。
- PEAK-MEM-01 PR #29: head `da8991d`、merge `106ceb7`。PR run
  `30680771633`、post-main run `30681027479`はUbuntu / Windows / macOS 15でPASS。

## Decisions / residual risks

- byte上限8/2 MiBとfinal 8 MiBは維持する。newline上限はその内側の独立境界。
- 1,000,000 / 250,000は単一hostのRSSから逆算せず、通常長の100,000行paragraph
  互換と境界回帰を根拠に固定した。strictなprocess-memory保証ではない。
- streaming parserは、大規模互換要件が具体化した場合だけ別Class Lで検討する。
- commit直前recheckとreplaceの間のlost-update windowは既存どおり残る。
- policy-rejected cleanupを再試行せず、保持中のlocal branch / worktree / ignored residueを
  通常のcleanup候補へ戻さない。

## Key files / next steps

- `scripts/merge_section.py`, `scripts/test_merge_section.py`
- `scripts/measure_peak_memory.py`, `docs/peak-memory-characterization.md`
- `README.md`, `SECURITY.md`, `SKILL.md`, `docs/SKILL.ja.md`
- `scripts/validate-oss-readiness.ps1`, `CHANGELOG.md`

1. CHECKOUT-V7-01のsource integrationは完了。新しい再現可能な契約不備、Issue、
   PR feedback、CI failureがなければ別repoへ進む。
2. 大規模互換要件が具体化した場合だけ、streaming parserを別Class Lで検討する。
3. Release / tagはowner gateのまま実行しない。
4. 保持中のlocal branch / worktree / ignored residueを変更せず、同じcleanup failure classを
   再試行しない。
