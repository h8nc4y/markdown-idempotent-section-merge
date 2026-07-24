# Private-marker scanner hardening

## 目的

公開前scannerが、ambient Git設定、indexとworktreeの競合、link、巨大入力、
停止しないchild process、表示制御文字のいずれを受けても、見かけ上のcleanを
返さないようにする。既存のMarkdown merge実装と26件のPython回帰testは変更せず、
scanner境界だけを強化する。

## 影響範囲

- `scripts/scan-private-markers.ps1`: scan対象の確定、Git blob読取、検出、報告。
- `scripts/private-marker-process.ps1`: Windows JobとPOSIX process groupによる
  bounded child実行。
- `scripts/test-scan-private-markers.ps1`: synthetic hostile fixtureとcross-platform回帰。
- `.editorconfig`: 日本語意図コメントをWindows PowerShell 5.1でもparseする
  UTF-8 BOM契約。
- `.github/workflows/validate.yml`: Windows、Ubuntu、Windows PowerShell 5.1の実行契約。
  Windows上の2 suiteを直列完走させるため、job全体は25分でboundedにする。
- `README.md`、`SECURITY.md`、`CHANGELOG.md`: 利用者向け保証範囲と限界。

## 設計

1. callerの全`GIT_*`を信用せず、known/unknown/present-emptyを含めchild-only環境へ
   正規化する。Git protocol、replace object、lazy fetch、hook、filter、traceの
   外部作用を遮断する。
2. tracked pathごとにindex stage/debugを厳密に解析し、single
   `git cat-file --batch`でblobを読む。regular worktree snapshotを別に読み、
   indexとworktreeのunionを走査する。
3. intent-to-add、conflict、gitlink、symlink、reparse、root不一致、
   metadata drift、invalid UTF-8はfail closedにする。
4. Git probeでvalid worktreeを確立できない状態でscan rootまたはancestor直下に
   `.git` file/directoryがあればfail closedにする。一方、確定したnon-Git
   fallback root内のnested `.git` directoryとleaf `.git` fileはGit control
   metadataとして除外し、内容や外部targetをfollowしない。
5. Windowsではsuspended `CreateProcessW`をJobへ割り当ててからresumeする。
   launch failure時はterminateまたはJob closeとbounded waitの結果を検証し、
   synthetic failure fixtureでPID消失とtarget未実行を確認する。
   POSIXでは`setsid`または同一hostの`libc` gateでprocess groupを作り、
   errnoを確認してgroup全体を停止する。最初のWindows起動経路でもnative
   stdin/stdout/stderrをtextへ変換せず、`00/80/FF`をbyte-exactに中継する。
6. runtime、process、entry、target、byte、line、regex match、finding、stdout、
   stderrの各上限を独立して持つ。診断pathはcontrol、bidi/format、logical line
   separatorをescapeし、実OSの改行を含めたserialized byte数を上限内に収める。
7. 034固有のGitHub URL allowlistは本リポジトリ自身だけとする。既存のdotfile
   fallbackと`.env`、`.pem`、`.key`、`.npmrc`、extensionless textの走査を維持する。
8. workflowのthird-party actionはfull commit SHAへ固定し、major versionは
   review用commentとして残す。

## 検証計画

- Windows PowerShell 7: readiness、scanner self-test、repository scan。
- Windows PowerShell 5.1: readiness、scanner self-test、repository scan。
- official Ubuntu PowerShell: readiness、scanner self-test、repository scan。
- self-test先頭で初回Windows atomic launch経路を通し、stdin/stdout/stderrの
  `00/80/FF` byte-exactを検証。
- Python reference implementation: 既存26件を全件実行。
- YAML parse、PowerShell AST parse、`git diff --check`、Gitleaks、Semgrep。
- macOS実機は未確認とし、POSIX fallbackはUbuntuとsynthetic fixtureで測る。

## 完了条件

- 上記3 runtimeのscanner gateとPython 26件が成功する。
- public文書、workflow、検証scriptが同じ契約を示す。
- source freeze後の独立reviewでP1/P2/P3が0件になる。
- review完了まではcommit、push、PR、mergeを行わない。

## 残リスク

marker scannerは既知の高signal patternを検出するbest-effort safety netであり、
すべてのsecret形式を保証するものではない。macOS実機での挙動は未確認。
