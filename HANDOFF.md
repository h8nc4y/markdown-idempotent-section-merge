# HANDOFF

## Current goal

完了: 正本 block 内の possible setext H1/H2を初回appendで受理し、
次回replaceだけが拒否してapply-twiceに収束しないgapをClass Mで修正。
PR #12をmainへ統合し、3 OSのPR/main CIまで確認した。

## Delivered

- `validate_block`でtarget spanと同じsetext guardを再利用し、
  append / replaceを同じ固定診断・終了コード2・no-writeへ統一。
- `=` / `-`、0〜3 space、末尾space/tab、fence/raw HTML literal、
  非反射診断、UTF-8 block、BOM/CRLF targetのAPI / CLI回帰を追加。
- README、英語版/日本語版SKILL、専用契約文書を同期。
- PR #12をsquash merge。mainは`51d1db8`、feature commitは`77e1030`。

## Decisions

- 完全なMarkdown parserは追加せず、既存のconservative span scanを再利用。
- block内のpossible setextは自動変換せず、初回書込み前にfail closed。
- fence/raw HTML内のsetext風リテラルは見出しとして数えない。
- diagnosticは行番号以外を固定し、入力由来の見出し本文を反射しない。
- public fixtureはsyntheticに限定し、実データ・secretを含めない。

## Verification

- 独立review: tree `5d10b345` / patch SHA-256 `dfdc903e`一致、
  P0 / P1 / P2 / P3 = 0、clearance YES。
- PR CI run `30236164620`: Windows / Ubuntu / macOS 15すべてSUCCESS。
- main CI run `30236477455`: Windows / Ubuntu / macOS 15すべてSUCCESS。
- post-main Python: 144 PASS / 14 skip。
- post-main OSS readiness: PS7 / PS5.1ともPASS。repository
  private-marker scanもPASS。
- private-marker self-test: PS7 175.1秒 / PS5.1 121.4秒でPASS。
- Gitleaks: history 16 commits / 702.51 KB、worktree 978.86 KB、
  0 leaks。Semgrep: Python 2 files / 151 rules / 0 findings。
- main `51d1db8`とorigin/main一致、tracked tree cleanを確認。

## Key files

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `docs/setext-block-heading-contract.md`
- `README.md` / `SKILL.md` / `docs/SKILL.ja.md`

## Next steps

1. main上でopen issue / PR、CI、TODO、既知制約を再確認する。
2. 安全で価値の高い次のClass S/M改善を選び、別branchで進める。
