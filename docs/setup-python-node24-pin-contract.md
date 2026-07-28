# setup-python Node.js 24 immutable pin 契約

## 目的

GitHub Actionsの3 OS検証で発生しているNode.js 20廃止予告を、警告抑止用の
環境変数ではなく、Node.js 24を宣言する公式`actions/setup-python` releaseへの
immutable pin更新で解消する。

## baseline

- main CI run `30339961655`のWindows / Ubuntu / macOS 15はすべて成功したが、
  各check annotationが
  `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`
  をNode.js 20対象actionとして明示した。
- checkout stepやrunner自体は同じannotationの対象に含まれない。
- 現行workflowとreadiness validatorはsetup-python v5の同じ40桁SHAを
  canonical lineとして固定している。

## 一次情報と採用revision

2026-07-28にGitHub公式repository / release / commitを確認した。

- 最新release `v7.0.0`はimmutable releaseとして公開され、tagはcommit
  `5fda3b95a4ea91299a34e894583c3862153e4b97`を直接指す。
- 同commitはGitHub上で署名検証済みで、40桁SHAをworkflowへ固定できる。
- 同commitの`action.yml`は`runs.using: node24`を宣言する。
- GitHubはNode.js 20対象actionの利用者へ、Node.js 24で動作する最新action
  versionへの更新を案内している。
  https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/

公開repository URLはprivate repository名の混入を防ぐscanner契約に従って
tracked docsへ再掲せず、公式owner / repository名、release tag、40桁commit、
`action.yml`の実測値をsource identityとして固定する。

## 実装契約

- setup stepを次のcanonical lineへ置き換える。
  `uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0`
- `python-version: '3.x'`、step名、順序、3 OS matrix、25分timeout、
  read-only permission、Windows PowerShell 5.1 scopeは変更しない。
- `actions/checkout`やほかのactionは、今回のannotation原因ではないため変更しない。
- readiness validatorのexpected workflow、required step block、exact immutable
  revision assertionを同じreviewed changeで更新する。
- validator self-testは少なくともmutable major `actions/setup-python@v7`と、
  旧v5 SHAへのdowngradeをin-memory mutationし、両方をfail closedで拒否する。

## 受入条件

- PowerShell 7 / Windows PowerShell 5.1のOSS readinessが成功し、上記2 mutationが
  実際に拒否される。
- Python reference tests、repository private-marker scan、Gitleaks、Semgrep、
  `git diff --check`が成功する。
- PR / post-main CIでWindows / Ubuntu / macOS 15が成功する。
- PR / post-mainの3 check annotationsに、setup-pythonのNode.js 20廃止予告が
  0件であることをGitHub APIで確認する。
- 全private-marker scanner self-testのローカル再実行は、shared host競合を避ける
  root指示を優先し、明示枠なしでは行わない。CIのbounded jobで検証する。
