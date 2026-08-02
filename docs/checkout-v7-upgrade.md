# checkout v7.0.1 immutable pin 更新契約

## 分類と目的

- **分類:** Class M（supply-chain pinとexact workflow validatorの小規模更新）。
- **目的:** validation workflowの`actions/checkout`を、公式v7.0.1のverified full
  commit SHAへ更新する。mutable tagへ依存せず、人間向けversion commentと実pinのdriftも
  fail closedで検出する。
- **影響範囲:** `.github/workflows/validate.yml`、
  `scripts/validate-oss-readiness.ps1`、`README.md`、`CHANGELOG.md`、Living Handoff。

## Baselineとprovenance

- 2026-08-02時点でlocal main、origin、live mainは
  `8c9f6d1b9b4c6e9ad0757bca052209c7c09e3653`で一致し、tracked treeとstashは空。
- exact-main run `30694827311`はWindows / Ubuntu / macOS 15の3 jobが成功した。
- 現行pin `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09`は公式v5.1.0 tagを
  直接指す。採用する公式v7.0.1 tagはverified commit
  `3d3c42e5aac5ba805825da76410c181273ba90b1`を直接指す。
- v7.0.1 releaseはdraft / prereleaseではないがimmutable表示はfalseである。このためtagでは
  なく40桁commit SHAをworkflowへ固定する。
- v5.1.0とv7.0.1の`action.yml`はいずれも`runs.using: node24`を宣言する。

## 実装契約

- checkout stepを次のcanonical lineへ置き換える。
  `uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
- workflow trigger、`contents: read`、step名と順序、`persist-credentials: false`、
  setup-python pin / input、3 OS matrix、25分timeout、Windows PowerShell 5.1 scopeは変更しない。
- readiness validatorのexpected semantic lines、required checkout block、exact revision assertionを
  同じreviewed changeで更新する。
- validator self-testはmutable `actions/checkout@v7`、旧v5.1.0 SHA、正しいSHAに古い
  version commentを付けたstale-comment mutationをin-memoryで作り、すべて拒否する。
- LINE-BUDGET-01の保持中worktree / branch / ignored cacheには触れず、policy-rejected cleanupを
  再試行しない。Release / tagはowner gateのまま実行しない。

## 検証計画

- validator-first TDDとして、workflowを変更する前にPowerShell 7 / 5.1 readinessのREDを
  確認し、canonical pin更新後に両方GREENへ戻す。
- Python 176 tests、PowerShell 7 / 5.1 readiness、private-marker self-test / actual scan、
  Gitleaks、Semgrep、UTF-8 / whitespaceを確認する。
- 独立read-only reviewを2系統実施し、PR / post-main CIでWindows / Ubuntu / macOS 15の
  3 jobとcheck annotationを確認する。

## Handoff

- **状態:** baseline / provenance / compatibility確認済み。validator-first REDは両PowerShellで
  2 diagnostics、v7.0.1 canonical pin更新後は3 negative mutationを含めて両方GREEN。
  Python 176 testsと両runtimeのprivate-marker self-test / actual scanも成功した。
  Gitleaksはworktree finding 0、Semgrep `p/security-audit`は6 targets / 2 rulesで
  finding 0。`actionlint`はhostに未導入のため未実行・未確認。独立code reviewのP2は、
  mutable / old-pin mutationで正しいversion commentを保ちrefだけを壊すよう分離して修復し、
  両PowerShell readinessはGREEN。P2修復後の独立再review 2系統はP0〜P3すべて0。
  exact staged GitleaksとSemgrep 6 targets / 2 rulesはfinding 0。
- **未確認:** v7変更後のPR / main CI。production、deployment、OAuth、secret、実データ、
  paid operation、Release / tagは実行しない。
