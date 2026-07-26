# closing-hash 管理対象 H2 の同一性契約

## 目的

列 0 の素の `## Name` を正本にする文書で `## Name ##` が存在すると、
従来は別の行として見落とし、素の H2 を追記して意味上の重複を作れた。
CommonMark 0.31.2 の ATX heading では、所定の空白条件を満たす末尾の
`#` 列は見出し本文から除外される。この Class M 修正は、その
block-level alias を書込み前に拒否する。

## 影響

- 管理対象ブロックの先頭行に closing sequence があれば、正本として受理せず
  plain form への修正を要求する。
- target の frontmatter、fence、raw HTML の外側で、正本 H2 の直後に
  1 個以上の space / tab、1 個以上の unescaped `#`、末尾の space / tab
  だけが続く行を closing-hash alias とする。
- CommonMark が ATX heading に許す 0〜3 個の先頭 ASCII space も同じ候補にする。
- alias が 1 件でもあれば通常実行と `--check` を固定診断・終了コード 2・
  no-write で fail closed にする。正本への自動変換や重複節の自動削除は行わない。
- 4 個以上の先頭 space、先頭 tab、空白で区切られていない末尾 `#`、
  `#` 列の後に本文が続く行は alias にしない。
- inline Markdown のレンダリング結果までは比較しない。exact raw heading に
  closing sequence だけを足した既知の block-level alias に限定する。

## 検証

- closing sequence の長さ、末尾 space / tab、0〜3 space indentation を変えた
  単独候補を API で拒否する。
- 素の正本 + alias と複数 alias を拒否する。
- block 側の closing sequence を拒否する。
- frontmatter、fence、raw HTML 内の文字列を過剰拒否しない。
- 4 space、先頭 tab、`## Name#`、`## Name ### body` を過剰拒否しない。
- 通常 CLI と `--check` が CRLF / BOM を含む元 bytes を維持する。
- 既存 fixture、apply-twice、OSS readiness、private-marker scanを維持する。

## 一次仕様

- [CommonMark 0.31.2 - ATX headings](https://spec.commonmark.org/0.31.2/#atx-headings)
