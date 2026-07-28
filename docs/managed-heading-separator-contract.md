# 管理対象 H2 の separator alias fail-closed 契約

## 目的

CommonMark 0.31.2 の ATX heading は、opening sequence 後の raw content を
inline parse する前に先頭・末尾の ASCII space / tab を除く。したがって、
正本 `## Managed` に対する `##  Managed`、`##	Managed`、
`## 	 Managed` は raw bytes が違っても同じ H2 本文を持つ。

この Class M 修正は、正本 block 側で非canonical separatorを初回書込み前に
拒否し、target 側では同じ本文を持つ separator alias を意味上の別見出しとして
追記しないよう fail closed にする。

## 影響

- 非空の正本 H2 は、opening `##` と本文の間を ASCII space 1個だけにする。
  複数space、tab、space/tab混在は固定診断・終了コード2・no-writeで拒否する。
- 既存の空 H2 `##` は維持する。opening後がASCII space/tabだけの行も、
  既存どおり末尾空白を除いて空 H2 `##` へ正規化する。
- targetでは、frontmatter、fence、raw HTMLの外側にあるATX H2だけを候補にする。
  0〜3個の先頭ASCII space、既存のclosing-hash sequence、末尾ASCII
  space/tabを考慮した後、先頭・末尾ASCII space/tabを除いたraw contentが
  正本本文と完全一致し、separatorだけが非canonicalなら書込み前に拒否する。
- 1〜3space indentだけ、またはclosing-hashだけの既存aliasは、それぞれの
  固定診断契約を維持する。separator aliasと組み合わさった場合も見落とさず
  fail closedにする。
- 4個以上の先頭space、先頭tab、`##Managed`、`## Managed#`、
  inline Markdownの別表現は、この限定的な同一性判定へ含めない。
- NBSP、EM SPACE、form feed、vertical tabなどの非ASCII whitespaceは
  separatorやtrim対象にしない。自動変換、reindent、重複削除も行わない。

## 検証

- 現行実装がcanonical blockへ`##  Managed` / `##	Managed`を別節として
  appendすることをsynthetic REDで固定する。
- block側の複数space、tab、space/tab混在をappend / replaceの両方で拒否する。
- target側の複数space/tab、0〜3space indent、closing-hashとの組合せ、
  canonical + alias、複数aliasをAPIで拒否する。
- frontmatter、fence、raw HTML内の同じ文字列を候補に数えない。
- 固定診断へUTF-8の見出し本文を反射しない。
- 通常CLIと`--check`でBOM + CRLF targetをbyte単位で維持する。
- exact canonical、空 H2、`##Managed`、非closing hash、
  非ASCII whitespaceの既存契約を維持する。
- Python full、PowerShell 7 / Windows PowerShell 5.1 readiness、
  private-marker scan、Gitleaks、Semgrep、3 OS CIを通す。

## 一次仕様

- [CommonMark 0.31.2 - ATX headings](https://spec.commonmark.org/0.31.2/#atx-headings)
  - opening sequenceはspace / tabまたは行末に続く。
  - raw heading contentはinline parse前に先頭・末尾のspace / tabを除く。
  - Example 67はopening `#`後の多数spaceが本文`foo`へ畳み込まれることを示す。
