# macOS CI 契約

## 目的

README が案内する macOS / POSIX 実行経路を、Linux での代替確認だけでなく
GitHub-hosted macOS 15上で継続検証する。

## 受入条件

- 既存の Windows / Ubuntu matrixを維持し、`macos-15`を追加する。
- macOSでもPowerShell 7でOSS readiness、reference implementation、
  private-marker self-test / scan、committed-tree whitespaceを同じ順序で実行する。
- Windows PowerShell 5.1 stepは既存どおりWindows runnerだけで実行する。
- 全matrix jobは既存の25分timeoutとread-only `contents` permissionを維持する。
- action revision、scanner timeout、検出範囲を変更しない。native失敗で実装差が
  判明した場合は、固定診断とcross-platform回帰を先に作り、最小修正だけを行う。
- scannerのGit root判定は、Gitが返すworktree内判定とroot相対prefixを同じ
  bounded processで取得する。exact `true`と空prefixだけを受理し、macOSの
  `/var/...`と`/private/var/...`のような同一rootの祖先aliasは文字列比較しない。
  repo subdirectory、bare / Git directory、malformed recordはfail closedにする。
- readinessは`validate` job内でmatrix、`${{ matrix.os }}` runner、25分timeoutを
  同じ構造として検証し、PS5.1 stepのname / condition / shellも同じstepへ固定する。
  `validate`はroot `jobs:` mapping内に所属させ、別jobやblock scalar内の正しい
  文字列だけでは合格させない。root / contract mappingはcanonical simple key
  （unquoted key、colon直結、固定indent）へ限定し、同じ意味のquoted / spaced /
  explicit / tagged key、非canonical親へのnest、key重複を値にかかわらず拒否する。
  `validate` job本体は既知の閉じたline/value集合も検査し、multiline quoted /
  flow scalar本文へ正しいcontract文字列を退避しただけでは合格させない。
  trigger / permission preludeと、checkout、Python setup、readiness、reference
  tests、scanner self-test / scan、Windows-only PS5.1、whitespaceの各stepを
  name + uses / shell + run/bodyの同じ順序で1回ずつ固定する。
  root keyは`name` / `on` / `permissions` / `jobs`、direct jobは`validate`
  だけに固定し、workflow拡張時は実装とvalidatorを同じreviewed changeで更新する。

## 証跡境界

- local Windowsで既存PS7 / PS5.1 gateとworkflow静的契約を確認する。
- `runs-on`固定化、macOSを除いたmatrix＋block scalar decoy、反転したPS5.1
  condition＋block scalar decoyをin-memory mutationし、readiness自身が拒否する。
- `validate`をroot block scalarへ移した非実行job、重複`runs-on`、
  重複`timeout-minutes`、inline / quoted / spaced形式の重複root `jobs`も拒否する。
  1 / 3-space親へ正しい文字列をnestしたdecoyも拒否し、CRLFとroot commentは
  受理する。unexpected root / sibling job追加はfail closedにする。multiline quoted scalarへjob本文全体を
  退避するdecoy、root `name`からworkflow全体をscalar化するdecoy、各必須stepの
  削除・並べ替え・root scalarへのcommand退避も拒否する。
- native macOSの証跡はPRとpost-mainのGitHub Actions runで取得する。
- runnerが失敗した場合はログの固定診断だけを扱い、timeoutやscanner境界を
  証拠なしに緩めない。
- PR #8の初回run `30208443602`ではUbuntu / Windowsが成功し、macOS 15だけ
  private-marker self-testの`git-root-mismatch`で失敗した。このREDをroot
  identity回帰として保持し、修正後runとpost-main runでGREENを確認する。

## 一次情報

- GitHub Docs「GitHub-hosted runners reference」（2026-07-26確認）:
  `macos-15`は標準GitHub-hosted macOS ARM64 runner labelとして掲載されている。
  https://docs.github.com/en/actions/reference/runners/github-hosted-runners
