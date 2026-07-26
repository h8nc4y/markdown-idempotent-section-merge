# HANDOFF

## Current goal

進行中: Class Mとして、CommonMark closing-hash形式の管理対象H2を別物として
追記できるidentity gapと、Unicode whitespaceをASCII space/tabとして削る
block grammar gapを修正する。

## Success metrics

- block先頭のclosing-hash H2をplain form要求で拒否する。
- targetの0〜3 space closing-hash aliasをliteral region外だけで拒否する。
- 通常実行 / `--check`を終了コード2・固定診断・no-writeへ揃える。
- heading trim、blank / setext、fence closer、closing suffixをASCII space/tab
  契約へ統一し、Unicode whitespace bytesをcontentとして保持する。
- CRLF / BOM、既存fixture、apply-twice、3-platform CIを維持する。
- source freeze後の独立reviewでP1 / P2 / P3を0件にする。

## Key files

- `scripts/merge_section.py`
- `scripts/test_merge_section.py`
- `docs/closing-hash-managed-heading-contract.md`
- `docs/commonmark-ascii-whitespace-contract.md`
- `README.md` / `SKILL.md` / `docs/SKILL.ja.md`
- `CHANGELOG.md` / `scripts/validate-oss-readiness.ps1`

## Decisions

- CommonMark 0.31.2 §4.2のblock-level closing sequenceだけを同一性候補にする。
  inline Markdownのレンダリング結果までは比較しない。
- aliasを自動変換・削除せず、書込み前に固定文言で拒否する。
- 4+ space、先頭tab、空白で区切られない末尾`#`、closing sequence後に
  本文が続く行は既存どおり別物とする。
- CommonMark block grammarのwhitespaceはASCII space/tabに限定する。
  Pythonの引数なし`strip` / `rstrip`は入力由来textへ使わない。
- NBSP、EM SPACE、form feed、vertical tabは自動正規化せずcontentとして保持する。
- public-facing fixture / diagnosticsはsynthetic・非反射とする。

## Current verification

- main `1970265`とorigin/main一致、tracked tree clean、open issue / PR 0、
  最新main CIはWindows / Ubuntu / macOS 15がSUCCESSと確認して開始した。
- 実装前にAPI / CLI回帰で11 failureを取得した。
- 最小実装後、closing-hash / indented identity / CLI対象testはPASS。
- Python full suiteはUnicode修正後140件PASS / 14件skip。
  py_compile、diff checkがPASS。
- OSS readinessとrepository private-marker scanはPS7 / PS5.1ともPASS。
- private-marker self-testはUnicode修正後PS7 164.5秒 /
  PS5.1 111.5秒でPASS。repository scanも両engineでPASS。
- Gitleaksは履歴663.75 KBとworktree 967.28 KBで0 leaks。
- Semgrep p/python + p/secretsはPython 2 files / 187 rules / 0 findings。
- 初回freeze `266c519d`の独立reviewはP1=0 / P2=1 / P3=0。
  引数なしUnicode `strip` / `rstrip`が別heading、末尾content、setext、
  fence closer、closing suffixを誤判定する再現を確認し、clearanceは保留。
- NBSP単独回帰6件を実装前に全FAILで取得。ASCII-only helper実装後、
  NBSP / U+2003 / U+000C / U+000BのAPI 9観点とBOM+CRLF CLI 3観点はPASS。
- Unicode修正後のPython full suite、readiness / scanner両engine、
  Gitleaks / Semgrep再scanはPASS。
- 新freeze再review、commit、push、
  PR / CI / merge、post-mainは未実施。

## Next steps

1. exact freezeを独立reviewへ渡す。
2. P1 / P2 / P3 = 0後にcommit / push / PR / CI / mergeを行う。
3. post-main検証、branch cleanup、中央dev-log同期で完了状態へ圧縮する。
