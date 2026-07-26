# HANDOFF

## Current goal

Class M: CommonMark raw HTML block 内にある `# ...` / `## ...` リテラルを
実見出しと誤認せず、HTML block と後続節を壊さない見出し走査へ修正する。

## Success metrics

- トップレベル・列0〜3の CommonMark HTML block type 1〜7 を見出し走査から
  除外する。
- type 1〜5 の未クローズ入力は書込み前に終了コード 2 で fail closed にする。
- HTML 終了後の H1/H2 と既存 frontmatter/fence/setext 境界を変えない。
- LF/CRLF、UTF-8 BOM、apply-twice、CLI の既存契約を維持する。
- 全ローカル検証、独立レビュー、GitHub Actions を通してから統合する。

## Key files

- `docs/html-block-heading-scan-contract.md`
- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `SKILL.md` / `docs/SKILL.ja.md`
- `README.md` / `CHANGELOG.md`

## Decisions

- CommonMark 0.31.2 の HTML block type 1〜7 をトップレベル範囲で扱う。
- fence と HTML は単一の逐次状態機械で走査し、互いの delimiter を無視する。
- CommonMark は type 1〜5 の未終端を EOF 終了にするが、本ツールは mutation
  safety のため意図的に fail closed にする。
- case-insensitive 判定も CommonMark の ASCII tag/attribute grammar に限定し、
  Unicode case-fold lookalike で opener/closer を成立させない。
- possible link reference definition + `===` の後は setext/paragraph 文脈を
  部分走査で断定せず fail closed。複数行 link label は escape-aware に追跡し、
  未escape `]` の直後が colon でなければ通常段落へ戻す。単純な definition +
  type 7 は inline のまま。
- container block 完全解析、同名 H2 の同一性正規化、macOS CI は別タスク。

## Baseline evidence

- OSS readiness: pass
- merge tests: 76 tests / 14 skip（残り62件pass）
- private-marker scanner self-test: pass
- private-marker scan: pass

## Current verification

- 設計契約を先行更新済み。
- merge tests: 114 tests / 14 skip（残り100件pass）。
- OSS readiness: PowerShell 7 / Windows PowerShell 5.1 pass。
- py_compile / Semgrep 151 rules / Gitleaks: 0 findings。
- private-marker self-test/scan はP2修正後の全差分で再実行し、pass。freeze
  manifest は HANDOFF 更新後の follow-up scan 通過を条件に作る。
- 独立レビューで旧 P1 Unicode case-fold、単一行 reference+setext、
  HANDOFF stale は解消確認済み。追加P2の複数行 link label は赤テスト2件を
  再現後に修正し、focused API/CLI no-write 検証が pass。通常 bracket text
  の過剰拒否も赤テスト1件から修正済み。
- 次の再レビューP2 2件（行末単一backslashの取り逃し、colonなし未escape `]`
  後の過剰拒否）も赤テストから修正。escaped `\]` の単一行過剰拒否も追加で
  赤→green を確認済み。

## Next steps

新しい完全 manifest は HANDOFF 更新後の follow-up private-marker scan を
通過した差分から作り、独立再レビューを行う。
P1/P2/P3=0確認後、commit/push/PR/CI/merge/main 同期/cleanup。
次ループ候補は1〜3空白インデント同名 H2、その後に macOS CI。
