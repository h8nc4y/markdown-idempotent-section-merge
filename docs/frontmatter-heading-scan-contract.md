# Frontmatter-aware heading scan

## 目的

Markdown 文書の先頭 frontmatter 内に `## Managed` のような文字列があっても、
管理節の実見出しとして置換しない。現行の fence-aware 走査はコードフェンスだけを
除外するため、frontmatter 内の疑似見出しから次の H1/H2 までを削除し、
frontmatter の closing delimiter を失わせる可能性がある。

## 影響とClass

Class M の内容破壊バグ修正。公開 API、CLI 引数、原子的書込み方式は変更せず、
見出し・境界・setext 検査で無視する領域を文頭 frontmatter へ拡張する。

## 正本契約

1. frontmatter opener は文書の先頭行、列 0 の完全一致だけを認識する。
   - YAML: `---`
   - TOML: `+++`
2. YAML closer は列 0 の完全一致 `---` または `...`。TOML closer は
   列 0 の完全一致 `+++`。
3. opener から最初の対応 closer まで、delimiter 行を含む全行を
   H1/H2 見出し、同名見出し、setext、コードフェンスの走査対象から除外する。
4. 対応 closer が無い場合は frontmatter と thematic break のどちらかを
   推測せず、書込み前に `MergeError` で fail closed にする。
5. 先頭以外の `---` / `+++` は frontmatter delimiter にしない。
   frontmatter 外の thematic break と通常の H1/H2 境界は従来どおり扱う。
6. canonical block は従来どおり列 0 の `## Heading` で始まり、frontmatter を
   持たない。CLI と公開関数は同じ fail-closed 契約を共有する。

## テスト計画

- YAML の `---` / `...` closer、空行を含む本文、TOML `+++` の内部にある
  同名 H2 リテラルを無視し、frontmatter 全体を byte 内容として維持する。
- frontmatter 後の実 H2 は置換し、2回目の適用を `unchanged` にする。
- YAML/TOML の未クローズと、空白付きの非 exact closer を拒否する。
- 先頭以外の thematic break と後続 H1/H2 の既存境界を維持する。
- CLI の通常実行と `--check` が未クローズ入力を終了コード 2 で拒否し、
  ターゲット bytes を変更しない。
- frontmatter を含む LF/CRLF と UTF-8 BOM の保持を既存 file-level 契約で確認する。
- 全 3 fixture 不変条件（期待出力、apply-twice、見出し数）を回帰させない。

## 今回の対象外

- raw HTML block 内の疑似見出し。次の改善候補として fail-closed または
  HTML block-aware 走査を別変更で検討する。
- 1〜3 空白インデントの同名 H2 と列 0 canonical H2 の意味的重複。
  HTML block 対応後の候補として別変更で検討する。
- 一般 YAML/TOML parser、文書途中の frontmatter、複数 frontmatter 文書。
