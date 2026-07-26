# CommonMark block 文法の ASCII whitespace 契約

## 目的

Python の引数なし `strip` / `rstrip` は、NBSP、EM SPACE、form feed、
vertical tab なども whitespace として削除する。一方、CommonMark 0.31.2 の
blank line、ATX heading のtrim / closing sequence、fenced code block のcloserが
許す末尾whitespaceは ASCII space / tab に限定される。この差を放置すると、
別見出しや実contentを同一視して置換・削除したり、無効なfence closerを受理して
保護対象のliteral regionを誤走査したりできる。

## 影響

- block文法で「blank」や「末尾空白」を判定するときは、共通のASCII-only
  helperを使う。入力由来textへ引数なし `strip` / `rstrip` を使わない。
- 見出しの末尾は space / tab だけをtrimする。Unicode whitespaceが続く
  `## Managed<NBSP>`は、素の`## Managed`と別のraw headingとして保持する。
- Unicode whitespaceだけの行をblankとして削除しない。append前のtarget末尾でも
  setext underline直前でも実contentとして扱う。
- fence delimiter後のUnicode whitespaceはcloserの末尾空白ではない。open fenceを
  維持し、target / blockがそのままEOFへ達したらmutation前にfail closedにする。
- closing hash後のUnicode whitespaceはclosing sequenceの許容末尾ではない。
  `## Managed ##<NBSP>`を素の管理対象H2のaliasとして過剰拒否しない。
- Unicode whitespaceをASCIIへ自動変換しない。bytesを保存するか、曖昧な構造なら
  固定診断で拒否する。

## 検証

- NBSP（U+00A0）、EM SPACE（U+2003）、form feed（U+000C）、
  vertical tab（U+000B）を同じtable-driven回帰へ通す。
- APIでtarget / block heading、target末尾、setext、target / block fence、
  closing-hash後の各境界を検証する。
- CLIの通常実行と`--check`をBOM + CRLF targetで検証し、別見出しのbytes保持、
  canonical block追記、unclosed fence / setextの終了コード2 / no-writeを固定する。
- ASCII space / tabの既存trim、fence close、apply-twice契約を維持する。
- Windows、Ubuntu、macOSの同じCI matrixで実装差を継続検証する。

## 一次仕様

- [CommonMark 0.31.2 - Characters and lines](https://spec.commonmark.org/0.31.2/#characters-and-lines)
- [CommonMark 0.31.2 - ATX headings](https://spec.commonmark.org/0.31.2/#atx-headings)
- [CommonMark 0.31.2 - Setext headings](https://spec.commonmark.org/0.31.2/#setext-headings)
- [CommonMark 0.31.2 - Fenced code blocks](https://spec.commonmark.org/0.31.2/#fenced-code-blocks)
