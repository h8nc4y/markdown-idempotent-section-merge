# HANDOFF

## Current goal

進行中: Windows所有権移譲後のtemporary保持テストをlive filesystemの
metadata揺らぎから分離し、production fingerprintを緩和せず決定的にする。

## Delivered

- baseline: main CI `30343770882` attempt 1だけWindowsで、所有権移譲テストが
  `_commit_temporary`到達前に`target metadata changed during merge`で停止した。
  同一SHA attempt 2と現main `c1785c2`のrun `30345451320`は3 OS成功。
- 所有権移譲テストは`_assert_target_unchanged`をexact-argument spyへ分離し、
  target guard → commit境界の順序と、例外後artifact保持だけを検証する。
- 別のcross-platform testはtarget bytesを変えずmtimeだけを固定値へ変更し、
  production fingerprint差、metadata診断、commit helper未呼出しを検証する。
- README / CHANGELOGへ責務分離とPython timestamp精度前提を同期した。

## Decisions

- `scripts/merge_section.py`と`_stat_fingerprint`は変更しない。
- Python 3.14 docsどおり`*_ns`の表現とfilesystem実精度を区別する。
  微小差やsleepを使わず、FATの2秒粒度でも区別できる2001-01-01のmtimeを使い、
  実際のfingerprint差をassertしてからproduction guardを通す。
- Windowsの`st_ctime(_ns)`は現状creation timeでdeprecatedのため、
  test mutationの主信号にせずmtimeを使う。production fingerprintからは外さない。
- upstreamは`origin/main`のためplain pushを使わず、topic branchを明示してpushする。

## Verification

- focused 2 tests: PASS。
- root再検証のfull suite: `Ran 153 tests`、`OK (skipped=14)`。
- PowerShell 7 / Windows PowerShell 5.1のOSS readiness: PASS。
- focused 100反復ずつ: 200 / 200 PASS、skip 0。
- owned mutation: ownership transfer前の`temporary = None`を外すと1 / 1 RED
  （artifactがouter cleanupに削除される）。復元後production diff 0。
- changed 4 files: strict UTF-8、LF、NULなし。`git diff --check` PASS。
- local host: Python 3.11.15。Python 3.14 / `py` launcherはPATH上で利用不可。
- Python 3.14.6公式docsで`os.utime(ns=...)`とfilesystem依存のtimestamp精度を確認。
- repo scanner self-test / actual scanはPowerShell 7と5.1の双方でPASSし、
  各実行前後のscanner processは0。
- Gitleaks / Semgrepはfinding 0。独立reviewはP0〜P3=0、CLEARANCE YES。
- ignored `scripts/__pycache__`はcleanup commandが実行前にpolicy拒否されたため、
  再試行せず保持する。tracked / untracked差分には含まれない。
- changed treeの3 OS PR CI、stage / commit / push / PR / merge、post-main: 未確認。

## Key files

- `scripts/test_merge_section.py`
- `README.md`
- `CHANGELOG.md`
- `HANDOFF.md`

## Next steps

1. exact 4ファイルをstageし、global pre-commit guard経由でcommitする。
2. topic branchを明示pushし、base `main`のPRで3 OS CIを確認する。
3. merge後にpost-main検証とbranch / worktree cleanupを行う。
