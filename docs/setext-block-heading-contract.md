# block 内 setext 見出しの fail-closed 契約

## 目的

正本 block に `見出し` + `===` / `---` の setext H1/H2 が含まれると、
従来の初回 append はその block を受理できた。一方、次回 replace は同じ
setext 見出しを置換スパン内の境界候補として拒否するため、同じ入力を2回
適用しても no-op に収束せず、apply-twice 契約が破れる。

この Class M 修正は、target を変更する前の `validate_block` で同じ構造を
拒否し、初回と次回の validation 境界を一致させる。

## 影響

- block の先頭にある正本 ATX H2 より後で、literal region 外の非blank行に
  `===` / `---` 下線が続く場合は、possible setext H1/H2 として拒否する。
- target の置換スパンで使う conservative scan と同じ判定を再利用する。
  完全な Markdown parser は導入せず、疑いを安全側へ倒す既存方針を維持する。
- fence、raw HTML 内の setext 風リテラルは拒否しない。block は正本 H2
  から始まるため、文書先頭専用の frontmatter 構文は block 内では扱わない。
- 通常実行と `--check` は同じ固定診断・終了コード 2・no-write で停止する。
  診断へ block 本文、見出し名、ファイル内容を反射しない。
- UTF-8、BOM、LF / CRLF の target bytes を変更しない。setext 見出しは
  ATX 形式へ変換するか、固定 begin/end marker 方式を選ぶ。

## 検証

- 空 target への append と既存 target の replace の両方を API で拒否する。
- `=` と `-`、0〜3 space indentation、末尾 space / tab を検証する。
- 日本語を含む synthetic block、BOM + CRLF target を CLI 通常実行と
  `--check` に通し、終了コード 2 と元 bytes 完全一致を確認する。
- fixed diagnostic が入力由来 marker を含まないことを確認する。
- fence、raw HTML 内の setext 風リテラルを過剰拒否しない。
- 既存 fixture、apply-twice、OSS readiness、private-marker scanを維持する。

## 一次仕様

- [CommonMark 0.31.2 - Setext headings](https://spec.commonmark.org/0.31.2/#setext-headings)
