# Markdown Idempotent Section Merge（Markdown 節の冪等マージ）

これはリポジトリルートの [SKILL.md](../SKILL.md)（英語・正典）の日本語版です。内容が
食い違う場合は英語版を正とします。

Markdown 文書内のひとつの正本節（`## 見出し` ブロック）を自動更新で維持するための
手順です: 節が既存なら置換、無ければ追記し、同じマージを2回適用したら2回目は
バイト単位で無変更（冪等）にします。非自明なのは節が**どこで終わるか**の判定で、
古典的な失敗は素朴な `^##` 正規表現をその境界判定に使うことです。

## いつ使うか

- エージェントやスクリプトが、自分が全体を所有していない文書 — `README.md`・
  `AGENTS.md`・`CLAUDE.md`・ハンドブック・changelog の前文など — の正本節を
  「既存なら置換・無ければ追記」ルールで維持しているとき。
- 次の症状が見えたとき:
  - 維持対象の節が実行のたびに複製される。
  - マージがフェンス付きコードブロックの途中で節を切断した。
  - コードフェンス**内**の `## ...` 行が「次の節」として扱われた。
  - `###` 小見出しで置換範囲が早期終了した。
  - 更新が収束しない: 2回目の実行でもファイルが変わり続ける。
  - マージ後、ある地点から下が全部ひとつの巨大なコードブロックとして
    レンダリングされる（取り残されたフェンス区切りが新しいフェンスとして
    再オープンした）。
- 節を `^##` 系パターンで**読む**・**数える**ときも同じ罠を踏みます —
  抽出と検証も対象です。

## 罠: コードフェンス内の見出し

素朴な実装は置換範囲を「`## X` 行 〜 次に `^##` がマッチする行の直前」で
取ります。これは2通りに壊れます。

**罠1 — フェンス内リテラル。** 節本文には正当な理由で `## ...` 行がフェンス付き
コードブロック内に含まれます: レポートのテンプレート、サンプル文書、引用した
diff など。フェンスを見ない走査は、その最初の1行を「次の節の境界」と誤読して
範囲をそこで切ります:

````markdown
## Automation notes

The bot refreshes this section.

```text
## Weekly report        <- 素朴な ^## 走査はフェンス内のここで止まる
- highlights:
- risks:
```

Keep the template fenced so it does not become a real heading.

## License              <- 本当の次の節
````

フェンス内の行までで置換すると、古い末尾が取り残されます。実測された壊れ方
（テスト済みの罠 fixture を参照）は単純な重複よりも悪質です:

- フェンス内だった `## Weekly report` リテラルがフェンスの**外**に取り残され、
  本物の（重複に見える）見出しとしてレンダリングされる。
- 古いブロックの閉じフェンス区切り（裸の `` ``` `` 行）も取り残され、**新しい**
  フェンスとして再オープンし、それ以降を全部飲み込む — 後続の `## License`
  節は見出しですらなくなる。
- マージが冪等でなくなる: 実行のたびに見出しが再マッチし、フェンス内の行で
  再び切断され、ファイルが成長し続ける。

**罠2 — `###` 小見出し。** 裸の `^##` 正規表現は `###` や `####` にも
マッチします。節に小見出しがあると範囲が最初の `### ...` 行で終わり、古い
小節が新しい小節の後に生き残ります — マージ地点より下が重複コンテンツに
なります。

この2つの罠は、節を**読む**（内容を抽出する）ときと**検証する**ときにも同じ
ように効きます（`grep -c '^## X'` はフェンス内リテラルを過剰カウントも誤認も
します）。

## 安全な境界: 2方式

### 方式1 — literal region 対応の見出し走査

行を走査しながら literal region 状態を追跡し、frontmatter・フェンス・対応
raw HTML block の外にある行だけを見出し・境界として扱います:

- フェンスは「先頭の非空白文字（先行空白は最大3個）がバッククォート3個以上
  またはチルダ3個以上の連なり」の行で開く。
- 閉じるのは「**同じ文字**が**開いたとき以上の長さ**で連なり、他に何もない」
  行だけ。閉じられなかったフェンスは文書末尾まで続く（CommonMark の挙動）。
- フェンス内では、いかなる行も見出しにも境界にもならない。
- フェンス走査の前に、文書先頭の完全一致 frontmatter を除外する。YAML は
  `---` で始まり、完全一致 `---` または `...` で閉じる。TOML は完全一致
  `+++` で始まり `+++` で閉じる。その中の見出し風の行は metadata/comment
  であって Markdown 節境界ではない。対応 closer が無い opener は書込み前に
  fail closed にする。
- トップレベル・列0〜3の
  [CommonMark 0.31.2 raw HTML block](https://spec.commonmark.org/0.31.2/#html-blocks)
  type 1〜7 も除外する。literal-content tag
  （`pre`/`script`/`style`/`textarea`）、comment、processing instruction、
  declaration、CDATA、列挙 block tag、complete tag 単独行を扱う。type 6/7 は
  空行直前または EOF で終わり、type 7 は段落を割り込まない。fence と HTML は
  1本の排他的走査で扱い、互いの内部にある delimiter は literal data のままにする。
  tag/attribute alphabet は ASCII のみに限定し、Unicode case-fold で似る文字を
  ASCII tag として受理しない。
- mutation safety のため CommonMark より意図的に厳しくする。type 1〜5 の
  opener に explicit end condition が無ければ fail closed。対象外の
  container/indent 文脈直後に type 7 候補があり判定が曖昧な場合も推測しない。
  possible link reference definition + `===` で setext 終了を証明できない場合も
  fail closed にする。複数行 link label は escape を考慮して追跡し、最初の
  未escape `]` の直後が colon でなければ通常段落へ戻す。空行なしの単純な
  definition + tag は inline HTML のまま。
- 節の境界は次の**レベル1または2**の見出し。素朴形は `^##[^#]`
  （`###` を除外）で、参照実装はこれを `^ {0,3}#{1,2}([ \t]|$)` に強化
  している: H1 も境界として扱い（部の境界を置換に巻き込んで消しては
  ならない）、CommonMark が許す1〜3スペースのインデントを受け付け、
  CommonMark のスペース規則で `##hashtag` のような段落行を偽境界に
  しない。`###` はどちらの形でも境界ではない。

文書フォーマットを自分で変えられないとき、任意の Markdown に対して動く
ツールが必要なときはこの方式を使います。

### 方式2 — 固定マーカー

維持する節を一意なセンチネル行で挟みます — HTML コメントはレンダリング時に
不可視です:

```markdown
<!-- managed-section:begin automation-notes -->
## Automation notes

...本文。フェンス付きテンプレートも自由...
<!-- managed-section:end automation-notes -->
```

begin/end マーカー行（完全一致）の間を丸ごと置換し、begin マーカーが無ければ
マーカー込みのブロック全体を追記します。範囲決定から見出し走査が完全に
消えます。

文書フォーマットを自分が所有しているならこの方式が最頑健です（見出しの
改名にも任意の本文にも耐える）。コストはマーカーを植えること。マーカー文字列は
節ごとに一意にし、この方式でも下記の単一 H2 不変条件は守ってください —
マーカーが守るのは**範囲**であって、文書の見出し構造ではありません。正直な
注意点をひとつ: 別の場所のコードフェンス内にマーカー行のリテラルコピーが
あると、フェンスを見ないマーカー検索はやはり誤認します。マーカー文字列を
フェンス内の例に書かないか、マーカー検索自体も同じフェンス対応走査で
行ってください。

## 不変条件

すべてのマージの前後でこれらを強制します。「置換 or 追記」が well-defined に
なるのはこの条件下です:

1. **ブロックは自身の素の `## 見出し` 行で始まる** — 見出しはマージされる
   内容の一部であり、内容と乖離しうる設定値にしない。CommonMark の
   closing-hash sequence はここでは正本にしない。
2. **ブロック内の H1/H2 レベル見出しはちょうど1個** — 自身の1行目の
   ATX 見出し（literal region 対応で数える）。2個目の ATX / setext H1/H2 は
   後続実行で節を吸収または分断し得る。初回書込み前に possible setext 見出しを
   拒否し、append と replace の validation 境界を一致させる。ブロック内の
   フェンス/raw HTML 内の見出し風リテラルは問題ない — カウントされない。
3. **ターゲット内の曖昧でない見出しはたかだか1個**（literal region 外）。
   文書が既に重複、1〜3 space版、closing-hash版を含んでいるなら、片方だけ
   「修復」してもう片方を残すのではなく、停止して報告する。
4. **正規のセパレータ形状。** ブロックは末尾空行なしで保持し、後続の節との
   間には空行ちょうど1個を書く。読み取り範囲と書き込み形状が同じ正規形に
   収束することが、2回目の実行を no-op にする。
5. **未クローズのフェンスを、ターゲットにもブロックにも許さない。**
   CommonMark では閉じられなかったフェンスは文書末尾まで続くため、
   ターゲット側では節が静かに EOF まで拡大し（置換が「見た目上フェンスに
   飲まれていた末尾」を丸ごと書き換えてしまう）、ブロック側ではマージした
   節の後続を全部飲み込む。どちらも壊れた入力として、停止して報告する。
6. **正本 block と置換スパン内に setext 見出しを許さない。** 段落行の
   直下の `===` / `---` 下線は、行単位の `^#` 走査では境界として見えない
   本物の見出し。新規 block で受理すると2回目が no-op に収束せず、置換で
   またぐと節境界がエラーなしに消える。どちらも書込み前に停止する
   （ATX 形式に変換するか、固定マーカーに切り替える）。詳細は
   [`setext-block-heading-contract.md`](setext-block-heading-contract.md)。
7. **文書先頭の frontmatter を未クローズのまま許さない。** 先頭行が完全一致
   `---`（YAML）または `+++`（TOML）なら、最初の完全一致 closer までだけを
   見出し走査から除外する。closer が無ければ metadata と通常 Markdown の
   thematic break のどちらかを推測せず停止する。
8. **explicit-end raw HTML の曖昧さを許さない。** CommonMark では type 1〜5
   の end condition が無い場合も EOF まで継続するが、そこへ H2 を追記すると
   raw HTML に飲まれて冪等性が壊れる。ターゲット/ブロック双方で終端を必須に
   する。type 7 は段落を割り込まない規則を守り、未対応 container/indent 文脈で
   判定できない場合や、複数行 link label を含む possible-reference + setext
   文脈なら fail closed にする。
9. **block whitespaceはASCII限定。** blank line、ATX headingのtrim /
   closing sequence、setext文脈、fence closerは、CommonMark 0.31.2どおり
   ASCII space / tabだけを使う。NBSP、EM SPACE、form feed、vertical tabなどを
   暗黙に削って別構造へ変えない。詳細は
   [`commonmark-ascii-whitespace-contract.md`](commonmark-ascii-whitespace-contract.md)。

## 検証レシピ

マージ後に3つのチェックをすべて実行します。境界バグ・節の重複・巻き込み
編集をまとめて捕捉できます:

1. **apply-twice-diff-zero。** 同じマージを2回適用し、2回目の後の
   `git diff` が空であること（または適用前後のバイト比較）。ファイル末尾に
   ある節なら「ブロックがファイルの末尾セクション全体と一致する」ことが
   同値のチェックになる。

   ```bash
   python scripts/merge_section.py target.md section.md
   python scripts/merge_section.py target.md section.md   # 2回目
   git diff --exit-code target.md   # 1回目をコミット済みなら: 空
   ```

   参照実装の `--check` モードは同じ考え方を diff 流の終了コード
   （0 = 正規形、1 = マージで変わる）で表現しており、CI からそのまま
   呼べます。

2. **見出し出現数 = 1。** 見出しと一致する行をフェンス対応の走査で数える。
   裸の `grep -c '^## Automation notes$'` はまさにこのスキルの罠の対象 —
   本文に見出しと完全一致するフェンス内コピーが無いと分かっているときだけ
   許容される。

3. **`git diff --stat` が対象1ファイルだけに触れている**こと。それ以外が
   出たら、自動化が書くべきでない場所に書いている。

## 参照実装

[`scripts/merge_section.py`](../scripts/merge_section.py) が方式1と上記の
全不変条件を、依存ゼロの Python 3（標準ライブラリのみ）で実装しています。
シェルではなく Python を選んだ理由はここで効く1点です: 改行と BOM を
バイトレベルで明示制御できるため apply-twice-diff-zero が証明可能になる
（シェルのテキストパイプラインは裏で改行を正規化しがち）。アルゴリズムは
どの言語にもそのまま移植できます。

変更内容はターゲットと同じディレクトリの排他的な一時ファイルへ全量を書き、
flush 後に1回の原子的置換で確定します。一時ファイルは、内容を書き込む前から
POSIX では mode `0600`、Windows では SYSTEM と Owner Rights だけに full
access を与える protected DACL で作成し、確定直前に identity・全バイト・
metadata・Windows DACL を再検証します。既存 POSIX ターゲットは
owner/group・権限ビット・上限付き拡張属性を維持します。既存 Windows
ターゲットは `ReplaceFileW` が文書化している DACL・ファイル属性・named
stream の引継ぎだけを利用します。未作成ターゲットは一時ファイルの非公開
権限を維持したまま作成されます。

ターゲットと正本ブロックの両方を、リンクをたどらない通常ファイルの
snapshot として読みます。シンボリックリンク、Windows reparse point、
EFS 暗号化済み Windows ターゲット、通常ファイル以外、複数ハードリンクを
持つファイルは、その意味の変更・blocking read・平文一時ファイルへの露出を
避けるため、内容の読込み前に拒否します。
確定直前にターゲットの identity・metadata・全バイトを再確認します。この
再確認より前に完了した変更は検出できますが、再確認と置換は別操作です。
既存ターゲットの lost update を防ぐ必要がある場合は、すべての writer を
外部 lock または単一 runner で直列化してください。未作成ターゲットは
no-replace で確定するため、並行する新規作成を上書きしません。Windows の
曖昧な部分失敗では `AtomicCommitError` と回復用 artifact を残します。
`AtomicCommitError.committed` は `True`・`False`・判定不能の `None` の
3状態です。cleanup は unlink 直前に identity を照合しますが、portable な
path-based unlink は identity 条件付き操作ではないため、最後の name-swap
window は残ります。予測困難な private name と、曖昧時に artifact を消さない
fail-closed 動作で緩和します。

既存 Windows ターゲットの owner は effective token の default owner と一致
している必要があります。owner/group/SACL の厳密な維持は保証しないため、
それらが必須の文書にはこの参照実装を使わないでください。

```bash
python scripts/merge_section.py TARGET.md SECTION.md            # その場でマージ
python scripts/merge_section.py TARGET.md SECTION.md --check   # ドリフト検知
```

`SECTION.md` が正本ブロックです: 1行目が正確な `## 見出し`。ターゲットの
LF/CRLF スタイルと UTF-8 BOM は保持され、ターゲットが無ければ作成されます
（空文書への追記）。壊れた入力 — ターゲット内の見出し重複・ブロック内の
余分な見出し・どちらか一方でも未クローズのフェンス/explicit-end raw HTML・
曖昧な type 7 文脈・文書先頭の未クローズ YAML/TOML frontmatter・CR のみの
改行・正本 block / 置換スパン内の setext 見出しの疑い — は推測で処理せず
終了コード 2 で停止します。完全一致の文書先頭 YAML
（`---` から `---` / `...`）と TOML（`+++` から `+++`）は見出し・
literal region 走査から除外するため、metadata/HTML 内リテラルを置換起点に
しません。
改行の混在を正規化しただけのときは `normalized` / `would-normalize` と
正直に報告し、「unchanged」とは言いません。

[`tests/fixtures/`](../tests/fixtures) は1ケース1フォルダで、それぞれ
`input.md`・`section.md`・`expected.md` を持ちます:

| Fixture | 証明すること |
| --- | --- |
| `trap-heading-inside-fence` | フェンス内の `## ...` リテラルが範囲を終わらせない |
| `frontmatter-heading-literal` | 文書先頭 YAML 内の疑似見出しを置換起点にしない |
| `html-block-heading-literal` | raw HTML 内の疑似 H1/H2 が範囲を途中で切らない |
| `append-missing-section` | 無い節はセパレータ空行1個つきで追記される |
| `replace-existing-section` | 既存の節はその場で置換される |
| `subheading-boundary` | `###` は節内に留まり、範囲は次の本物の `##` で終わる |
| `h1-boundary` | `#`（部見出し）は飲み込まれず、範囲をそこで終わらせる |

自己テストは依存なしで動き、CI にも含まれています:

```bash
python scripts/test_merge_section.py
```

契約テスト（期待出力・apply-twice-diff-zero・見出し数 = 1・CRLF/BOM の
バイト安定性）に加えて、このスイートは罠を**実測のまま**保ちます: フェンスを
見ない実装を `fence_blind_merge` として同梱し、罠 fixture で文書が壊れること
（後続の節が再オープンしたフェンスに飲み込まれ、フェンス内リテラルが脱走して
見出しとしてレンダリングされる）、冪等でないこと（2回目の適用でまたファイルが
変わる）、小見出し fixture で `###` の位置で範囲が切れることを assert して
います。誰かがスキャナを罠の形に「簡略化」したら、まずこのテストが落ちます。

## 制限事項

- 正本節の見出し自体は列0の素の `## Name` 形式であること。block 側の
  閉じハッシュ形式は拒否します。literal region 外に同じ見出し本文の
  `## X ##` があれば同一性が曖昧なため、通常実行と `--check` の双方を
  no-write で拒否し、自動変換や重複削除はしません。詳細は
  [`closing-hash-managed-heading-contract.md`](closing-hash-managed-heading-contract.md)。
- literal region 外で同名 H2 に1〜3個の ASCII spaceが付いている場合は、
  管理対象見出しの同一性が曖昧なため、通常実行と `--check` の双方をno-writeで
  拒否します。list/containerかもしれない内容を自動reindentしません。詳細は
  [`indented-managed-heading-contract.md`](indented-managed-heading-contract.md)。
- Setext 見出し（`見出し` + `===`/`---` の下線）は境界になりません。
  正本 block または置換スパン内にある疑いがあるときは、初回だけ受理して
  次回に失敗したり、静かに消したりする代わりに書込み前に拒否します
  （不変条件6）。setext 見出しは ATX 形式に変換するか、固定マーカー方式に
  切り替えてください。詳細は
  [`setext-block-heading-contract.md`](setext-block-heading-contract.md)。
- frontmatter として認識するのは文書先頭・列0・完全一致だけです。YAML は
  `---` と `---` / `...`、TOML は `+++` と `+++` の組だけ。末尾に空白や
  文字がある delimiter、文書途中、他の metadata 形式は通常 Markdown として
  扱います。認識した opener に完全一致 closer が無ければ拒否します
  （不変条件7）。
- フェンス処理は CommonMark のコア規則の範囲（列0〜3のバッククォート/
  チルダフェンス、backtick を含む info string の除外を含む）。特殊ケース
  （blockquote 内や深いリストインデント内のフェンス）は参照実装のスコープ
  外です。
- raw HTML はトップレベル・列0〜3の CommonMark 0.31.2 type 1〜7を扱います。
  container prefix、lazy continuation、4文字以上の indentation は完全解析
  しません。その不明文脈直後の type 7 候補は拒否します。type 1〜5 は
  CommonMark より厳しく explicit end condition を必須にします。詳細は
  [`html-block-heading-scan-contract.md`](html-block-heading-scan-contract.md)。
- UTF-8 かつ LF または CRLF 改行の文書のみ。CR のみ（旧 Mac）の改行は
  拒否します — バイト冪等性が破れるため。CRLF と LF が混在するファイルは
  CRLF として扱われ（CRLF が1つでもあれば CRLF）、初回に `normalized`
  として一度だけ書き換えられ、2回目以降は安定します。
- ターゲットは未作成、またはハードリンク数1の通常ファイルに限ります。
  シンボリックリンクと複数ハードリンクのファイルは拒否するため、更新対象の
  通常ファイルを明示してください。
- 1回の実行で1ファイル・1節 — 検証を鋭く保つための設計です
  （`git diff --stat` = ちょうど1ファイル）。

## 出自

エージェント指示用 Markdown（README / AGENTS.md 系文書）の正本節を維持する
実運用から蒸留しました。維持対象の節が正当な理由でフェンス付きコードブロック
内に `## ...` レポートテンプレートを内包しており、罠1の壊れ方はフェンスを
見ないマージで実際に発生したものです。このリポジトリの fixtures と
`fence_blind_merge` テストがそれを再現し、失敗モードを逸話ではなく実測として
保っています。この文書自体 — `## ...` 行を含むフェンス付きの例 — が、素朴な
節ツールを壊すまさにその種類のファイルです。
