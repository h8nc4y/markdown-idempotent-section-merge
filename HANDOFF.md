# HANDOFF

## Current goal

Class M: READMEで案内するmacOS / POSIX経路を、GitHub-hosted `macos-15`
matrixで継続検証する。

## Success metrics

- Windows / Ubuntuを維持して`macos-15`をmatrixへ追加する。
- macOSでPS7 readiness、merge tests、private-marker self-test / scan、
  committed-tree whitespaceを同じ25分bound内で実行する。
- Windows PowerShell 5.1 step、action SHA、scanner timeout、検出範囲を維持する。
- native macOSで実測したroot alias誤判定だけを、Gitのworktree / prefix契約で修正する。
- native macOS実測はPRとpost-main runで取得する。

## Key files

- `.github/workflows/validate.yml`
- `scripts/validate-oss-readiness.ps1`
- `scripts/scan-private-markers.ps1`
- `scripts/test-scan-private-markers.ps1`
- `docs/macos-ci-contract.md`
- `README.md` / `CHANGELOG.md`

## Decisions

- `macos-latest`の将来image切替を避け、契約するnative imageを`macos-15`へ固定する。
- macOS固有失敗はnative runの実測から修正し、timeoutや検出境界を先回りで緩めない。
- rootの文字列表現同士は比較しない。1回のbounded Git probeで
  `--is-inside-work-tree=true`かつ`--show-prefix`空を厳密に要求し、祖先aliasを
  許容しながらrepo subdirectory / bare / Git directoryを拒否する。
- 直前taskのPR #7はsquash merge `486eace`、post-main run `30204599205`
  attempt 2でWindows / Ubuntu SUCCESS、main同期・cleanup済み。

## Current verification

- readinessへ`macos-15`静的契約を先行追加し、既存workflowに対するREDを実測。
- workflow / docs / living handoffを最小変更済み。
- merge tests 120件 / 14 skip、py_compile、workflow YAML parseがPASS。
- OSS readiness、private-marker self-test / repository scanはPS7 / PS5.1ともPASS。
- Gitleaks 13 commits / 624.06 KB / 0 leaks、diff checkがPASS。変更sourceは
  PowerShell / YAML / Markdownのみのため、global Semgrepに適用対象なし。
- 初回freezeの独立reviewで、workflow全体regexがblock scalar decoyと
  matrix未接続`runs-on`を合格させるP2を再現した。
- readinessをvalidate jobのindent構造検査へ変更し、runs-on固定化、
  matrix / PS5.1 conditionのblock scalar decoyをin-memory mutationで拒否。
- 再reviewで、`validate`のroot `jobs`所属未確認と異値duplicate keyのP2を再現。
  次のroot keyで`jobs`をboundし、contract keyを値に関係なく各1件へ固定した。
- 3回目reviewでquoted key、colon前空白、root `jobs`重複による実効YAML上書きの
  P2を再現。root / contract mappingをcanonical simple keyへ限定し、jobs key
  自体の重複と1 / 3-space親へのdecoy nestもfail closedにした。
- 未閉じmultiline quoted scalar内へ正しいjob文字列を退避すると旧line matcherが
  合格する追加adversarialを実測し、job値とblock本文も既知の閉じた形へ固定した。
- 4回目reviewでrequired PS7 step削除とroot `name` multiline scalarによる
  workflow全体退避のP2を再現。trigger / permission preludeと、全required stepの
  name + uses / shell + run/bodyをcomment/blank除外後のexact順序として固定した。
- 各step削除、readiness/reference入替、root command decoy、root/validate
  multiline scalarをreadiness自身のmutationで拒否する。
- 自己reviewで先行jobのmultiline scalarがvalidate text全体を飲むP2も再現。
  root keyを`name` / `on` / `permissions` / `jobs`、direct jobを`validate`
  だけへ固定し、unexpected root / sibling jobもfail closedにした。root commentは
  positive mutationで受理する。
- PS7 / PS5.1で正本・CRLF・root / validate comment・blankを受理し、plain / quoted / spaced /
  explicit / tagged key、inline root重複、非canonical親を含むmutationを拒否。
- 5回目reviewでraw line adjacencyによるcomment / blankのfalse-negativeと、
  root scalar自己変異の非anchored全置換を再現。全補助検査をsemantic line列へ
  統一し、root先頭行だけを置換するvalid YAML mutationへ修正した。PyYAMLで
  semantic root=`name`のみ / jobsなしを確認し、validatorは拒否した。
- 第6 exact freeze `a9f4b6f7`は独立review P1 / P2 / P3 = 0。組込み23変異、
  追加YAML adversarial、comment / blank正例も合格した。
- commit `027b831`をpushし、PR #8 / run `30208443602`を実行。Ubuntu / Windowsは
  SUCCESS、macOS 15だけprivate-marker self-testで
  `integrity: git-root-mismatch`となった。run logには絶対pathがないため具体的な
  2表記は推定だが、Gitのworktree canonicalizationとWindows PowerShell 7の
  `\\?\`同型回帰で、lexical root比較が原因となる機構を切り分けた。
- scannerをexact 2-record probeへ変更し、通常root scanはPS7 / PS5.1でPASS。
  Windows PowerShell 7のextended-length alias正例、repo subdirectory、
  non-worktree、non-empty prefixの負例をself-testへ追加した。
- 独立review P3を受け、missing final LF、extra record、NUL、invalid UTF-8、
  case / whitespace drift、root link、`.git` directory、bare repositoryの
  public-entrypoint負例を追加した。修正後のprivate-marker self-testは
  PS7 176.7秒 / PS5.1 118.0秒でPASS。
  readiness / repository scanも両hostでPASSし、Python 120件 / 14 skip、
  workflow YAML parse、diff checkがPASS。
- Gitleaks 910.45 KB / 0 leaks、Semgrep p/secretsは変更PowerShell 2 files /
  36 rules / 0 findings。変更6 filesはstrict UTF-8、LF、意図したBOMだけ、
  NUL / FF / U+FFFD / trailing whitespaceなし。

## Next steps

scanner修正後のPS7 / PS5.1全gate、security / text hygiene、独立reviewを再実行し、
PR #8へ追加commitをpushする。native macOSを含む全jobs成功後だけmerge /
post-main / cleanupする。
