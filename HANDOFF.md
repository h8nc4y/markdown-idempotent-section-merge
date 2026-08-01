# HANDOFF

## Current goal

LINE-BUDGET-01（Class M）を完了し、raw byte上限だけでは抑えられない高密度Markdownの
target / block入力を、各入力自身のUTF-8 decode / line-state list生成前にfail closed
する。Release / tagはowner gateのまま触れない。

## Current state

- branch: `fix/line-count-budget`。baseはPR #29 merge
  `106ceb779b94561335cba6b122c57df9b3165ecc`。
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
  PR/CI/post-main、cleanupまで実測する。

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
- PEAK-MEM-01 PR #29: head `da8991d`、merge `106ceb7`。PR run
  `30680771633`、post-main run `30681027479`はUbuntu / Windows / macOS 15でPASS。

## Decisions / residual risks

- byte上限8/2 MiBとfinal 8 MiBは維持する。newline上限はその内側の独立境界。
- 1,000,000 / 250,000は単一hostのRSSから逆算せず、通常長の100,000行paragraph
  互換と境界回帰を根拠に固定した。strictなprocess-memory保証ではない。
- streaming parserは、大規模互換要件が具体化した場合だけ別Class Lで検討する。
- commit直前recheckとreplaceの間のlost-update windowは既存どおり残る。

## Key files / next steps

- `scripts/merge_section.py`, `scripts/test_merge_section.py`
- `scripts/measure_peak_memory.py`, `docs/peak-memory-characterization.md`
- `README.md`, `SECURITY.md`, `SKILL.md`, `docs/SKILL.ja.md`
- `scripts/validate-oss-readiness.ps1`, `CHANGELOG.md`

1. 独立review findingを反映後、変更後full suite / readinessを再確認する。
2. Gitleaks / Semgrep、最終public-safety / diff reviewを直列実測する。
3. commit / push / PR / merge / post-main確認 / cleanupを行う。
