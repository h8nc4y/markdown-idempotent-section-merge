# Raw HTML block 見出し走査契約

## 状態

- 分類: Class M（境界走査の不具合修正）
- 対象: `scripts/merge_section.py`
- 基準仕様: [CommonMark 0.31.2 - HTML blocks](https://spec.commonmark.org/0.31.2/#html-blocks)
- 設計日: 2026-07-26

## 問題

現行の境界走査は先頭 frontmatter と fenced code block を除外する一方、
raw HTML block 内の `# ...` / `## ...` リテラルを実見出しとして数える。
そのため、管理対象節の途中に `<script>`、`<style>`、`<pre>`、HTML comment
などがあると、置換範囲を HTML block の途中で切り、閉じタグや後続節を
取り残し得る。

## 対応範囲

列 0〜3 から始まる、トップレベルの CommonMark raw HTML block 7種類を
見出し走査の literal region として扱う。先頭 frontmatter、fenced code block、
raw HTML block は1本の逐次状態機械で相互排他的に走査する。

1. `<pre` / `<script` / `<style` / `<textarea` で始まり、いずれかの対応系
   end tag を含む行まで。
2. `<!--` で始まり、`-->` を含む行まで。
3. `<?` で始まり、`?>` を含む行まで。
4. `<!` と ASCII letter で始まり、`>` を含む行まで。
5. `<![CDATA[` で始まり、`]]>` を含む行まで。
6. CommonMark 0.31.2 が列挙する block-level tag で始まり、次の空行の
   直前または EOF まで。
7. 1行で完結した open/closing tag だけの行で始まり、次の空行の直前または
   EOF まで。ただし CommonMark と同様、通常段落を割り込んで開始しない。

type 1 の end tag は CommonMark と同様、opener と同名である必要はない。
開始・終了判定は case-insensitive な規則では大文字小文字を区別しない。
ただし tag/attribute name の alphabet は ASCII に限定し、Unicode case-fold
で ASCII に等価になる `İ` / `ı` / `ſ` / `K` を tag 文字として受理しない。
type 6/7 の終端となる空行自体は HTML block に含めない。

この対応範囲はトップレベルの leaf block に限定する。blockquote や list item
内の container prefix、lazy continuation、4文字以上の indentation を含む
CommonMark 全構文解析は行わない。対象外構文を推測して置換範囲を広げない。

## fail-closed 契約

CommonMark 自体は type 1〜5 の終端が無ければ HTML block を EOF まで継続
させるが、この更新ツールでは mutation safety を優先する。認識済みの
type 1〜5 opener に対応する終端が無ければ、ターゲットと正本ブロックの
どちらも `MergeError`（CLI 終了コード 2）で拒否し、ターゲット bytes を
変更しない。

完全な type 7 tag として判定できない行は HTML block と推測しない。
ただし、認識済み explicit-end block が閉じているか判定できない状態は
通常 Markdown として処理せず、必ず fail closed にする。
また、possible link reference definition だけの段落に `===` が続くと、
definition の妥当性によって `===` が setext underline になるか新しい段落本文に
なるかが変わる。完全な reference parser を持たない本ツールは、この直後に
type 7 を開始する推測をせず fail closed にする。CommonMark の link label は
複数行にまたがれるため、possible label の開始から最初の未escape bracketまで
状態を維持する。行末 backslash は開始を無効化しない。最初の未escape `]` の
直後が colon なら possible definition とし、colon でなければ通常段落へ戻す。
未クローズ label のまま `===` に達した場合も definition ではないため通常の
setext として閉じる。空行なしで definition の直後に complete tag が来る
単純形は、CommonMark と同様に段落内 inline HTML とする。

## 状態遷移

1. 文頭の完全一致 frontmatter を先に確定する。
2. frontmatter 外を上から1回走査する。
3. fence 内では HTML opener を、HTML block 内では fence delimiter を
   状態遷移として解釈しない。
4. 通常状態では fenced code opener を先に、次に HTML type 1〜7 opener を
   CommonMark の優先順で判定する。
5. frontmatter / fence / HTML のいずれかに属する行は、H1/H2 出現数、
   節境界、setext 疑義判定から除外する。
6. HTML block が正常終了した次の実 H1/H2 は、通常どおり節境界にする。

## 受け入れ条件

- `<script>` / `<style>` / `<pre>` / `<textarea>` 内の疑似 H1/H2 を無視する。
- `<!-- ... -->` 内の疑似 H1/H2 を無視する。
- type 3〜7 について開始・終了規則を個別に固定する。
- Unicode case-fold 文字を ASCII tag/attribute と誤認せず、Unicode を含む
  end tag で ASCII type 1 block を閉じない。
- HTML block 終了後の同名 H2 を正本節として置換できる。
- HTML block 内の fence delimiter と setext 風下線が状態を漏らさない。
- possible link reference definition（単一行 / 複数行 label）+ `===` + type 7
  の曖昧な連鎖は API / CLI で fail closed とし、書込みを行わない。単純な
  definition + type 7 は段落を割り込ませない。
- 複数行 label 開始行の末尾 backslash を取り逃さず、未escape `]` が colon を
  伴わない通常 bracket text と、escaped `\]` を definition と誤認しない。
- 未クローズ type 1〜5 は API と CLI（通常 / `--check`）で fail closed し、
  書込みを行わない。
- fixture の期待全文、apply-twice-diff-zero、見出し出現数1を検証する。
- LF、CRLF、UTF-8 BOM を維持する。
- 既存 frontmatter / fence / setext / H1/H2 の回帰テストを維持する。
- 独立レビューで P1 / P2 / P3 がすべて0件になる。

## 今回に含めないもの

- container block 内 raw HTML の完全解析
- Markdown sanitizer や HTML 妥当性検証
- 1〜3空白インデントを含む同名 H2 の同一性正規化
- macOS の GitHub Actions job 追加
