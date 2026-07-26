# インデントされた管理対象 H2 の同一性契約

## 目的

列 0 の正本見出し `## Name` を管理対象にしている文書で、同じ文字列の H2 が
1〜3 個の ASCII space 付きで存在すると、従来はその行を別物として見落とし、
正本節を末尾へ追記して意味的な重複を作れた。この Class M 修正は、その曖昧な
入力を mutation 前に拒否する。

## 影響

- 管理対象ブロックの先頭行は、従来どおり列 0 の素の H2 に限定する。
- frontmatter、fence、raw HTML の外側で、末尾空白を除いた行が管理対象 H2 と
  一致する場合、先頭の ASCII space が 0 個なら正本候補、1〜3 個なら
  「インデントされた同一見出し候補」とする。
- インデントされた候補が 1 件でもあれば、通常実行と `--check` の両方を
  固定された説明文・終了コード 2・no-write で fail closed にする。
- 列 0 の候補が複数あれば、従来どおり重複として拒否する。
- 1〜3 space の行を自動的に列 0 へ移動しない。list/container 文脈を部分的に
  推測して文書構造を変えないためである。
- 4 個以上の space、先頭 tab、閉じハッシュ付き H2 は、列 0 の素の正本 H2 と
  同一視しない。
- frontmatter、fence、raw HTML 内の同じ文字列はリテラルであり、候補に数えない。

## 検証

- 1、2、3 space の単独候補を API で拒否する。
- 列 0 + インデント、複数インデント、list/container 風の候補を拒否する。
- 通常 CLI と `--check` が同じ固定エラーを返し、元 bytes を維持する。
- 4 space、tab、閉じハッシュ、literal region 内の文字列は過剰拒否しない。
- 既存 fixture、apply-twice、LF/CRLF/BOM、OSS readiness、security scan を維持する。
