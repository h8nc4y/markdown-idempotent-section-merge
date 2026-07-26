# HANDOFF

## Current goal

Class M: 1〜3 個の ASCII space が付いた同名 H2 を見落として正本節を重複追記
する経路を、mutation 前の固定エラー・no-write で fail closed にする。

## Success metrics

- literal region 外の列 0 は正本候補、1〜3 space は同一見出しの曖昧候補とする。
- 曖昧候補は API、通常 CLI、`--check` のすべてで拒否し、自動 reindent しない。
- 4+ space、tab、閉じハッシュ、literal region 内は過剰拒否しない。
- 既存 apply-twice、LF/CRLF/BOM、security/OSS readiness 契約を維持する。

## Key files

- `docs/indented-managed-heading-contract.md`
- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `README.md` / `SKILL.md` / `docs/SKILL.ja.md` / `CHANGELOG.md`

## Decisions

- list/container を完全解析していないため、1〜3 space 候補を列 0 へ移動しない。
- 4+ space、tab、閉じハッシュは正本の素の H2 と別物として既存契約を維持する。
- macOS CI は本修正へ広げず、別タスクとする。

## Current verification

- 設計契約を先行追加済み。
- focused 5 test / 8 failure をREDとして固定後、最小実装でGREEN。
- merge tests: 120 tests / 14 skip（残り106件pass）。
- OSS readiness: PowerShell 7 / Windows PowerShell 5.1 pass。
- private-marker self-test: PowerShell 7 / Windows PowerShell 5.1 pass。
- private-marker scan: PowerShell 7 / Windows PowerShell 5.1 pass。
- py_compile、Semgrep 79 rules / 2 files、Gitleaks: 0 findings。
- 独立レビュー、GitHub Actions は未実施。

## Next steps

diff/encoding確認 → exact freeze → 独立レビュー。P1/P2/P3=0後だけ
commit/push/PR/CI/merge/main同期/cleanupへ進む。
