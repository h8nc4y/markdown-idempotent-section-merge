# HANDOFF

## Current goal

進行中: 3 OS CIで発生する`actions/setup-python`のNode.js 20廃止予告を、
Node.js 24を宣言する公式v7.0.0 immutable commitへ更新して解消する。
workflow / validator / CI pin契約をClass M docs-firstで同期する。

## Delivered

- main CI run `30339961655`のWindows / Ubuntu / macOS 15 annotationsが、
  現行setup-python v5 SHAだけをNode.js 20対象actionとして明示した。
- main `66bc7d3`はorigin/mainと一致し、tracked tree clean、open PR / issueは各0。
- GitHub公式latest releaseは`actions/setup-python` v7.0.0。immutable tagは
  署名検証済みcommit `5fda3b95a4ea91299a34e894583c3862153e4b97`を直接指す。
- 同commitの`action.yml`が`runs.using: node24`を宣言することを確認した。
- 専用契約、README、macOS CI契約、CHANGELOGを実装前に同期した。

## Decisions

- workflowは40桁release commitと`# v7.0.0`をcanonical lineとして固定する。
- Python input、step順、matrix、timeout、permission、PS5.1 scopeは変更しない。
- warning対象でないcheckoutやrunner設定は変更しない。
- validatorは正本line更新だけでなく、mutable `@v7`と旧v5 SHAへのmutationを
  明示的に拒否する。
- public docs / PRには公式URLと固定SHAだけを載せ、raw CI logやローカルpathを
  掲載しない。

## Verification

- baseline main CI: 3 OS SUCCESS、setup-python Node.js 20 warningは各check 1件。
- official release / tag / commit / action metadata: 確認済み。
- docs-first差分以降のRED / GREEN、local gate、PR / main CIはこれから実測する。
- 既知のrepository外bounded log 5件はcleanup再試行禁止。今回も触れない。

## Key files

- `.github/workflows/validate.yml`
- `scripts/validate-oss-readiness.ps1`
- `docs/setup-python-node24-pin-contract.md`
- `docs/macos-ci-contract.md` / `README.md`

## Next steps

1. docs-first差分をcommitする。
2. validatorを先に新pinへ更新してREDを確認し、workflowを最小更新する。
3. focused gateとexact freeze review後、PR / 3 OS CI / merge / post-mainを完了する。
