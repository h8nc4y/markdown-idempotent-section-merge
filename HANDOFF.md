# HANDOFF

## Current goal

進行中: 正本 `## Managed` と意味上同じ `##  Managed` / `##	Managed` を
別見出しとしてappendするgapをClass Mで修正する。block側の非canonical
separatorは初回書込み前に拒否し、target側aliasはindent / closing-hashとの
組合せを含めてfail closedにする。

## Delivered

- read-only再現で、複数space、tab、混在separator、indent + separator、
  separator + closing-hashがすべてcanonical blockを末尾へappendすると確認。
- CommonMark 0.31.2 §4.2のraw heading content trimとExample 67を一次仕様として確認。
- baseline Pythonは144 PASS / 14 skip。main `1971b1e`はorigin/mainと一致し、
  open PR / issueは各0、直近main CIはSUCCESS。

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
- 実装後にfocused RED/GREEN、Python full、PS7 / PS5.1 readinessとscanner、
  Gitleaks / Semgrep、exact freeze独立review、PR / main CIを実測する。

## Key files

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `docs/managed-heading-separator-contract.md`
- `README.md` / `SKILL.md` / `docs/SKILL.ja.md`

## Next steps

1. 専用contract、README、英語版/日本語版SKILL、CHANGELOGをdocs-firstで同期する。
2. synthetic REDを追加し、既存identity contractを崩さない最小実装へ進む。
3. exact freezeを独立review後、PR / CI / merge / post-mainを完了する。
