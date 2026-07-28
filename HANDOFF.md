# HANDOFF

## Current goal

完了: 3 OS CIで発生していた`actions/setup-python`のNode.js 20廃止予告を、
Node.js 24を宣言する公式v7.0.0 immutable commitへの更新で解消した。
workflow / validator / CI pin契約をClass M docs-firstで同期し、PR #16をmainへ統合した。

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
- 専用契約をreadinessのrequired fileへ登録し、READMEのexact label +
  relative path linkも検証する。
- PR #16をsquash merge。main merge commitは`b9adbc9`。
- 統合証跡をPR #17で同期。closeout merge commitは`231e032`。

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
- Gitleaks: worktree約779.82 KB / history 24 commits・約748.73 KB、0 leaks。
  Semgrep `p/default`: 324 rules / 34 targets、0 findings。
- full private-marker scanner self-testはrootの明示枠がないためローカル未実行。
  PR / mainのbounded 3 OS CIで実行した。
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
- exact freeze: HEAD `7e97106` / tree `977e9b9` /
  stable patch-id `d69bba7e81ca786e43ff7c02404773ab89989a3a`。
  独立reviewはP0 / P1 / P2 / P3 = 0、clearance YES。
- PR CI run `30342150088`: Windows / Ubuntu / macOS 15すべてSUCCESS。
  GitHub APIで全3 checkのannotations / warnings / setup-python Node.js 20該当が
  各0件であることを確認した。
- main CI run `30342620338`: Windows / Ubuntu / macOS 15すべてSUCCESS。
  同じAPI集計で全3 checkのannotations / warnings / setup-python Node.js 20該当が
  各0件であることを確認した。
- closeout PR CI run `30343236403`: 3 OSすべてSUCCESS、全check annotations 0件。
- final main CI run `30343770882` attempt 1はUbuntu / macOSがSUCCESS。
  WindowsはPython 152 tests中1件でtarget metadata変化をfail closed検出した。
  同一treeのPR WindowsはSUCCESS、ローカルfocused testは20 / 20 PASS。
  failed jobだけをbounded rerunし、attempt 2は3 OSすべてSUCCESS。
  最新attemptの全check annotations / warnings / setup-python Node.js 20該当は各0件。
- post-main Pythonは152 PASS / 14 skip。PS7 / PS5.1 readinessとrepository
  private-marker scanもPASS。local main = origin/main = `231e032`、tracked tree clean。
- 既知のrepository外bounded log 5件はcleanup再試行禁止。今回も触れない。

## Key files

- `.github/workflows/validate.yml`
- `scripts/validate-oss-readiness.ps1`
- `docs/setup-python-node24-pin-contract.md`
- `docs/macos-ci-contract.md` / `README.md`

## Next steps

1. main上でopen issue / PR、CI、TODO、既知制約を再確認する。
2. 安全で価値の高い次のClass S/M改善を選び、別branchで進める。
