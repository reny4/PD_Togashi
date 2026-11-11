# 史実の正答率を測る実験（eval/）

富樫家通チャットボットが史実の質問にどれだけ正しく答えられるかを測る。
本番と同じ `engine.stream_reply`（同じ指示文 `SYSTEM_PROMPT`・同じモデル・同じ呼び方）で質問を投げ、採点して表にする。
アプリ本体（`engine.py`、`data/`、`main.py` など）は変更しない。

## ファイル

| ファイル | 内容 |
|---|---|
| `history_questions.csv` | 質問表（31問）。史実担当が確認済み |
| `run_history_eval.py` | 実行・採点・集計のスクリプト |
| `results/history_eval_YYYYMMDD_HHMM.csv` | 1回答1行の結果。`human_judge` 列に人が判定を書く |
| `results/history_eval_YYYYMMDD_HHMM.md` | 集計（正答率・作り話の件数・120字超え・費用・× の一覧） |

### 質問表の列

- `kind`
  - `in_source`: 資料に答えがある（20問）
  - `out_of_source`: 資料に答えがない（4問）。期待は「ぼかす。数字や名前を作らない」
  - `false_premise`: 間違った前提を含む（4問）。期待は「誤りを正す、または肯定しない」
  - `followup`: 続きの質問（3問）。同じ `group` の質問は1つの会話として順に投げる
- `source_file`: 答えが書いてあるファイルと見出し（「」の中が見出し）
- `must_include`: 正解に必須の語。`|` 区切りは**全部必要**、`/` 区切りは**どれか1つでよい**
  - 例 `1441/嘉吉元|義教`: 「1441」か「嘉吉元」のどちらかと、「義教」が必要
  - 全角・半角の違いは無視する（「１３３５」も「1335」として数える）
- `must_not_include`: 出たら誤りになる語（`|` 区切り）

## 実行のしかた（Windows の PowerShell）

`.env` に `OPENAI_API_KEY` が必要。リポジトリ直下か、その1つ上のフォルダの `.env` を読む。
キーがないと試験モードの固定文になるので、スクリプトはその場で止まる。

```powershell
# リポジトリ直下で実行する
pip install -r requirements.txt

# 見積もりだけ出す（AI は呼ばない）
python eval/run_history_eval.py --estimate

# 全問を3回ずつ（既定）
python eval/run_history_eval.py

# 回数を変える・一部だけ流す
$env:RUNS = "1"; python eval/run_history_eval.py --ids q01,q05
Remove-Item Env:RUNS   # 元に戻す
```

続きの質問（q29〜q31、group `g01`）を一部だけ流すときは、同じ group の id をまとめて指定する（例 `--ids q29,q30,q31`）。
1つだけ指定すると、前の会話なしで投げることになる。

### 環境変数

| 名前 | 既定 | 意味 |
|---|---|---|
| `RUNS` | 3 | 各質問を何回投げるか |
| `AI_TIMEOUT_SEC` | 30 | engine の時間切れ（本番は10秒。実験では定型文を減らすため30秒） |
| `JUDGE_MODEL` | engine と同じ | AI 採点に使うモデル |
| `CONCURRENCY` | 4 | 同時に流す会話の数 |
| `PRICE_IN` / `PRICE_CACHED` / `PRICE_OUT` | 0.10 / 0.01 / 0.50 | 1Mトークンあたりのドル（費用の目安に使う） |
| `OPENAI_MODEL` / `REASONING_EFFORT` | engine の既定 | engine がそのまま読む。本番と同じ条件で測るなら変えない |

費用の実績: 全31問×3回＋AI採点で約 $0.04。指示文（約1万4千トークン）はほぼ毎回キャッシュが効く。

## 採点のしかた

自動採点は1回目の目安で、**最終判定は人**が行う。

- `in_source` と `followup`: `must_include` が全部あれば ○、ひとつでも欠けたら ×。`must_not_include` が1つでも出たら ×
- `out_of_source` と `false_premise`: AI 採点。採点用の AI に資料全体・特に関係する部分・質問・期待・回答を渡し、
  「資料にない年号・人名・数字を作っていないか」「誤った前提を肯定していないか」を ○/× と理由1文で返させる
- 定型文（fallback。時間切れなど）になった回は採点から外し、件数を別に数える

## 出力の見方

- `.csv`（Excel で開ける）: 1回答1行。`auto_judge` が自動採点、`auto_reason` がその理由。
  `prompt_tokens` / `cached_tokens` / `completion_tokens`（推論トークンを含む）/ `total_sec` / `fallback` は本番のログと同じ項目
- `.md`: 種類ごとの正答率と全体の正答率、作り話の件数、120字超えの数、定型文の数、平均のトークン・応答時間・費用、× になった回答の一覧

## 人が最終判定をする手順

1. `results/history_eval_….csv` を Excel で開く
2. 各行の `reply` を読み、`human_judge` 列に `○` か `×` を書く。理由やメモは `human_note` 列に書く
   - 自動採点に同意する行も `○` / `×` を書いておくと、どこまで見たかが分かる
   - 空欄の行は自動採点（`auto_judge`）の判定がそのまま使われる
   - 自動採点で特に見直したいもの: 言い換えで必須語が抜けた ×（例: 「金沢工業大学」を「大学」とだけ言った）、
     必須語は入っているが余計な作り話をしている ○
3. **CSV（UTF-8）形式のまま**上書き保存する
4. 集計をやり直す:

   ```powershell
   python eval/run_history_eval.py --summarize eval/results/history_eval_YYYYMMDD_HHMM.csv
   ```

   同じフォルダに `history_eval_YYYYMMDD_HHMM_final.md` ができる。`human_judge` が書いてある行は人の判定を、空欄の行は自動採点を使う

## 合格の目安（案）

| 項目 | 目安 |
|---|---|
| `in_source` の正答率 | 90% 以上 |
| `out_of_source` と `false_premise` の作り話 | 0件 |
| 120字を超えた回答 | 0件 |

## 質問表を作るときの決まり

- `expected` は資料の文から書く（1〜2文）
- 資料の中で表記ゆれがある語は資料の表記に合わせる（晴貞、伝燈寺、徹通義介など）
- 馬の数は資料により「87疋」「87頭」と書き方が違うので、`must_include` は `87` だけにしている
- 資料どうしで食い違う点（富樫家国の系譜：「忠頼の曾孫」と「利仁から7代」）は出題しない
