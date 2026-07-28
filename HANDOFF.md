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
- workflowのsetup-pythonだけをv7.0.0の40桁SHAへ更新した。
- readiness validatorのcanonical workflow / exact revisionを同期し、
  mutable `@v7`と旧v5 SHAへのin-memory mutation拒否を追加した。

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
- docs-first commit: `de71157`。
- validator先行RED: PS7 / PS5.1ともexit 1。新immutable revision欠落と
  canonical workflow不一致の2診断で旧workflowを拒否した。
- workflow更新後GREEN: PS7 / PS5.1 readinessとrepository private-marker
  scanがPASS。Python fullは152 PASS / 14 skip、PowerShell BOMと
  `git diff --check`もPASS。
- Gitleaks: worktree約778.69 KB / history 21 commits・約744.65 KB、0 leaks。
  Semgrep `p/default`: 324 rules / 34 targets、0 findings。
- full private-marker scanner self-testはrootの明示枠がないため未実行。
- 初回freeze `d11b52ad`の独立reviewはP2=1 / clearance NO。新しい専用契約が
  `$requiredFiles`とREADME link assertionに未登録で、欠落/リンク切れが
  false-greenになると確認した。
- owned detached fixturesのRED: 専用契約欠落、README link破損の各mutantが
  修正前readinessでexit 0。
- 専用契約をrequired fileへ追加し、READMEのlabel + relative pathをassert。
  正本上のPS7 / PS5.1 readinessとrepository scan、Python 152 PASS / 14 skip、
  BOM、diff-checkは再度PASS。
- 修正後fixture GREEN: 契約欠落とlink破損をPS7 / PS5.1の各engineが
  exit 1と単一の固定診断で拒否した。fixture worktreeは削除済み。
- 再freeze review、PR / main CIはこれから実測する。
- 既知のrepository外bounded log 5件はcleanup再試行禁止。今回も触れない。

## Key files

- `.github/workflows/validate.yml`
- `scripts/validate-oss-readiness.ps1`
- `docs/setup-python-node24-pin-contract.md`
- `docs/macos-ci-contract.md` / `README.md`

## Next steps

1. P2修正のGREEN証跡をcommitする。
2. 新しいexact freezeで独立reviewを受ける。
3. PR / 3 OS CIでNode.js 20 annotation 0件を確認し、merge / post-mainを完了する。
