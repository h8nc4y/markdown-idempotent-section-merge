# Peak-memory characterization

- 計測日: 2026-08-01 JST
- 状態: PEAK-MEM-01 実測済み

## 目的

`merge_section.py` の 8 MiB target / final-output budget は raw I/O と永続化出力の
境界であり、process memory の上限ではない。短行多数、CRLF正規化、append、replace
で生じる増幅を合成Markdownだけで測り、次の安全対策を line-count budget と
streaming parser のどちらから始めるか判断する。

production実装、8 MiB / 2 MiBの既存byte contract、CLI APIは変更しない。計測値を
CIのmemory thresholdにも使わない。

## 計測契約

- `scripts/measure_peak_memory.py` が case × repetition ごとに fresh Python
  subprocessを1つ起動する。既定は5 cases × 3 repetitions。
- fixtureは64 KiB以下のchunkで生成し、target、block、finalは既存byte budget内に
  閉じる。実データ、secret、外部通信は使わない。
- 既定metricはprocess lifetimeのpeak RSS。Windowsは
  `GetProcessMemoryInfo().PeakWorkingSetSize`、macOS / Linuxは
  `resource.getrusage().ru_maxrss`を使う。
- process peakにはfixture生成とmodule importも含まれるため、mergeだけの厳密値では
  なく保守的なwhole-worker値である。
- `python-tracemalloc`は補助metricとして選べるが、高密度8 MiB caseでは大きな
  instrumentation overheadがある。
- workerごとのtimeoutは180秒、stdout / stderrは各64 KiB以下。stdoutはpath、fixture
  本文、環境変数を含まない単一JSON recordとする。
- action、byte budget、JSON schema、timeout、temporary artifactはpass / fail条件。
  peak MiB、増幅率、OS差は記述値であり、pass / fail条件ではない。

## Fixture matrix

| case | raw target bytes | final bytes | target lines | expected action |
| --- | ---: | ---: | ---: | --- |
| `lf-short-lines-append` | 8,388,586 | 8,388,608 | 4,194,293 | `appended` |
| `lf-short-lines-replace` | 8,388,608 | 8,388,608 | 4,194,296 | `replaced` |
| `crlf-short-lines-append` | 8,388,582 | 8,388,608 | 2,796,194 | `appended` |
| `crlf-short-lines-replace` | 8,388,608 | 8,388,608 | 2,796,197 | `replaced` |
| `mixed-eol-normalize` | 5,592,412 | 8,388,608 | 2,796,197 | `normalized` |

Block inputは全caseで21 bytesのcanonical H2だけを使う。LF / CRLF appendはseparatorと
final blockを逆算し、replaceは同byte長のold/new bodyを使う。mixed-EOLは先頭1行だけ
CRLF、残りをLFにして、contentが同一のままfinal CRLFがexact 8 MiBになるようにする。

## Windows / CPython 3.11 実測

環境は Windows 11 Pro build 26200、AMD64、CPython 3.11.15。計測JSONの
`platform.release()` raw値は`10`であり、marketing OS名として解釈していない。
各caseをfresh workerで3回実行した。
全15 samplesでaction、final exact 8 MiB、timeout内完了、temporary artifact 0を確認した。
計測開始・終了時の`measure_peak_memory.py` SHA-256は
`795C3392AA69ADF6F3CBA5E5E07DAB15F7A2687E7BBCF55DFD68C624C05E2807`で一致した。

| case | peak RSS MiB min / median / max | peak / input min / median / max | merge elapsed sec min / median / max |
| --- | ---: | ---: | ---: |
| LF append | 197.28 / 197.88 / 197.90 | 24.659671 / 24.735355 / 24.737796 | 19.70 / 19.73 / 19.77 |
| LF replace | 229.27 / 230.06 / 230.09 | 28.658131 / 28.757741 / 28.761647 | 51.03 / 51.91 / 52.91 |
| CRLF append | 150.11 / 150.12 / 150.14 | 18.763195 / 18.765148 / 18.768078 | 12.73 / 13.01 / 13.28 |
| CRLF replace | 171.29 / 171.38 / 171.39 | 21.410591 / 21.422798 / 21.423774 | 33.27 / 33.70 / 34.11 |
| mixed-EOL normalize | 165.32 / 165.34 / 165.51 | 30.998138 / 31.001800 / 31.032561 | 32.79 / 33.12 / 33.90 |

Windows以外、別Python version、別allocator、container制限下のprocess peakは未確認。
この表を他環境の保証値として扱わない。

## `tracemalloc` 補助診断

- exact 8 MiB LF append、4,194,293行は180秒のworker timeoutに達し、親側wall-clock
  観測は180.7秒だった。同条件を
  繰り返さず、full matrixの既定metricを低オーバーヘッドのprocess peakへ切り替えた。
- 1 MiB LF append、524,277行を1回測ると、traced peakは22,379,533 bytes、
  process peakは45,506,560 bytes、traced peak / inputは21.342806、merge elapsedは
  23.33秒。ここでelapsedは`merge_file()`呼出だけであり、fixture生成、import、
  subprocess起動、後処理を含まない。
  action、final exact 1 MiB、artifact 0は成功した。

`tracemalloc`値はPythonが追跡するallocationだけでありRSSではない。process peak値も
whole-worker値であり、両者を相互変換しない。

## 判断

1. byte budgetだけではpeak memoryを制限できない。exact 8 MiB LF replaceは、この
   Windows環境でmedian 230.06 MiB、max 230.09 MiBまで増幅した。
2. 同じfinal 8 MiBでも4,194,296行のLF replaceは2,796,197行のCRLF replaceより
   peakが高い。raw bytesだけでなくline densityが主要因である。
3. 次の安全な対策は **line-count budgetを先行**する。既存parserをstreaming化する
   より変更面が小さく、decodeとline/state list生成の前にfail closedできる。
4. exact cap値はこの単一hostのpeakから直ちに固定しない。代表的な通常Markdownの
   compatibility fixtureと、raw newline countの境界回帰を別task
   `LINE-BUDGET-01`で作り、cap・固定path-free診断・no-writeを同時に決める。
5. streaming parserはline-count budget後も必要な大規模互換要件が確認された場合の
   別Class L候補とする。

## 再現コマンド

Full 8 MiB process-peak matrix:

```powershell
python scripts/measure_peak_memory.py --repetitions 3 --timeout-seconds 180
```

CI向け縮小matrix:

```powershell
python scripts/measure_peak_memory.py --target-bytes 65536 --repetitions 1 --timeout-seconds 30
```

補助`tracemalloc`診断:

```powershell
python scripts/measure_peak_memory.py --metric python-tracemalloc --case lf-short-lines-append --target-bytes 1048576 --repetitions 1 --timeout-seconds 180
```
