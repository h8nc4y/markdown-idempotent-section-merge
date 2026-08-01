# HANDOFF

## Current goal

LINE-BUDGET-01（Class M）の統合済み状態と検証証跡を保持する。
policy-rejected cleanup residueは変更せず、Release / tagはowner gateのまま触れない。

## Current state

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

1. 現在のlocal-safe backlogはない。具体的な大規模互換要件が確認された場合だけ、
   streaming parserを別Class Lで検討する。
2. Release / tagはowner gateのまま実行しない。
3. 保持中のlocal branch / worktree / ignored residueを変更せず、同じcleanup failure classを
   再試行しない。
