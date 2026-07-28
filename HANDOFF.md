# HANDOFF

## Current goal

完了: 正本 `## Managed` と意味上同じ `##  Managed` / `##	Managed` を
別見出しとしてappendするgapをClass Mで修正。block側の非canonical
separatorは初回書込み前に拒否し、target側aliasはindent / closing-hashとの
組合せを含めてfail closed化し、PR #14をmainへ統合した。

## Delivered

- read-only再現で、複数space、tab、混在separator、indent + separator、
  separator + closing-hashがすべてcanonical blockを末尾へappendすると確認。
- CommonMark 0.31.2 §4.2のraw heading content trimとExample 67を一次仕様として確認。
- baseline Pythonは144 PASS / 14 skip。main `1971b1e`はorigin/mainと一致し、
  open PR / issueは各0、直近main CIはSUCCESS。
- canonical block separator validationとtarget alias scanを追加。既存の
  literal-region mask、closing-hash、indent契約を再利用し、inline Markdownは
  新たに解釈しない。
- API / CLI回帰で複数space/tab、0〜3 indent、closing-hash組合せ、
  UTF-8固定診断、BOM/CRLF no-write、既存境界を固定した。
- README、英語版/日本語版SKILL、専用契約文書を同期。
- PR #14をsquash merge。mainは`316a0cd`、feature commitsは
  `39b7645` / `2ce652a`。

## Decisions

- 完全なMarkdown parserは追加せず、既存のconservative span scanを再利用。
- 非空block H2はopening `##`後をASCII space 1個に限定する。空 H2 `##`は維持。
- targetは0〜3space indentとoptional closing-hashを考慮し、先頭・末尾の
  ASCII space/tabだけを除いたraw contentをexact比較する。
- fence/frontmatter/raw HTML内のseparator aliasは見出しとして数えない。
- 4+ indent、先頭tab、hashtag、非closing hash、非ASCII whitespaceを
  過剰に同一視しない。
- diagnosticは行番号以外を固定し、入力由来の見出し本文を反射しない。
- public fixtureはsyntheticに限定し、実データ・secretを含めない。

## Verification

- 未修正mainでsynthetic API再現: separator alias 5系統が`appended`。
- baseline Python: 144 PASS / 14 skip。
- focused RED: 新規8 tests中、契約維持2件だけPASS、24 subcaseがFAIL。
- focused GREEN: 8 PASS。indent 0〜3 × separator 7種 × closing 3種の
  synthetic cross-product 84件もPASS。Python full: 152 PASS / 14 skip。
- 独立review: tree `c57d3a79` / stable patch-id `85c65cc2`一致、
  P0 / P1 / P2 / P3 = 0、clearance YES。
- PR CI run `30338637459`: Windows / Ubuntu / macOS 15すべてSUCCESS。
- main CI run `30339006865`: Windows / Ubuntu / macOS 15すべてSUCCESS。
- post-main Python: 152 PASS / 14 skip。
- post-main OSS readinessとrepository private-marker scanは、
  PS7 / PS5.1ともPASS。
- private-marker self-testはfeature freezeでPS7 / PS5.1ともPASS、
  stderr 0。post-mainではshared host負荷を避ける指示に従い再実行していない。
- Gitleaks: worktree約1.00 MB / history 19 commits・約723 KB、0 leaks。
  Semgrep: Python 151 rules / 2 targets、0 findings。
- main `316a0cd`とorigin/main一致、tracked tree cleanを確認。
- 検証用bounded log 5件はapproval layerが削除を拒否したためrepo外へ残存。
  repository / worktreeには含まれない。

## Key files

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `docs/managed-heading-separator-contract.md`
- `README.md` / `SKILL.md` / `docs/SKILL.ja.md`

## Next steps

1. main上でopen issue / PR、CI、TODO、既知制約を再確認する。
2. 安全で価値の高い次のClass S/M改善を選び、別branchで進める。
