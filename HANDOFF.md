# HANDOFF

## Current goal

Class M: 文頭の YAML/TOML frontmatter 内にある `## ...` リテラルを
実見出しと誤認せず、frontmatter を壊さない見出し走査へ修正する。

## Success metrics

- 先頭行が正確に `---` の YAML frontmatter は、正確な `---` または `...`
  までを見出し走査から除外する。
- 先頭行が正確に `+++` の TOML frontmatter は、正確な `+++` までを除外する。
- 未クローズ frontmatter は書込み前に終了コード 2 で fail closed にする。
- frontmatter 外の thematic break、H1/H2、既存の fence/setext 境界を変えない。
- LF/CRLF、UTF-8 BOM、apply-twice、CLI の既存契約を維持する。
- 全ローカル検証、独立レビュー、GitHub Actions を通してから統合する。

## Key files

- `docs/frontmatter-heading-scan-contract.md`
- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `SKILL.md` / `docs/SKILL.ja.md`
- `README.md` / `CHANGELOG.md`

## Decisions

- opener/closer は列 0 の完全一致だけを frontmatter delimiter とする。
- YAML の closer は `---` と `...`、TOML は `+++`。
- 文頭 `---` が閉じられない入力は thematic break と推測せず fail closed。
- raw HTML block と 1〜3 空白インデントの同名 H2 は別タスクとする。

## Baseline evidence

- OSS readiness: pass
- merge tests: 76 tests / 14 skip（残り62件pass）
- private-marker scanner self-test: pass
- private-marker scan: pass

## Current verification

- merge tests: 84 tests / 14 skip（残り70件pass）
- focused frontmatter tests: 12 tests pass
- OSS readiness: PowerShell 7 / Windows PowerShell 5.1 pass
- py_compile / diff check / UTF-8・BOM・LF・NUL: pass
- Semgrep: 151 rules / 0 findings
- Gitleaks: 0 leaks
- independent review: P1=0 / P2=0 / P3=0

## Next steps

focused commit、push、PR、Windows/Ubuntu CI、merge、main 同期、branch cleanup。
次ループ候補は raw HTML block 誤境界、その後に1〜3空白インデント同名 H2。
