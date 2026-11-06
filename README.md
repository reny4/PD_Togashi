# 富樫家通チャットボット 試作完成版（統合）

6担当の成果を1つにまとめたアプリ。画面とAPIを同じサーバ（Cloud Run 1台）から出すので、QRコードは1つ、CORSの設定もいらない。
統合プラン: https://claude.ai/artifact/K87nhrAn27F6bT3McXxJXZ ／ API: 会話API仕様 v0.1 https://claude.ai/artifact/KQUBZWKMvZ4MaRovxjKyUz

| ファイル | 中身 | 元にしたもの（担当） |
| --- | --- | --- |
| `main.py` | サーバ。`/api/session` `/api/chat`（`?stream=1` でSSE） `/api/survey` `/admin/export/<表>.csv` と画面 | ops/min-deploy（ログ・運用） |
| `engine.py` | 指示文を組み立ててAIを呼ぶ。失敗したら定型文 | engine/chat_cli.py（会話エンジン） |
| `flow.py` | ボタンの流れと決まった文言（あいさつ、気分メニュー、歴史の話題、別れ） | 画面ワークフロー図2（画面） |
| `db.py` | ログの4表 sessions / turns / surveys / daily_usage | ログ設計（ログ・運用、アンケート） |
| `topic.py` | 話題の判定（ログの topic 列に使う） | engine/topic.py（会話エンジン） |
| `static/` | 画面。少しずつ表示、残り◯回、スマホ幅 | screen/prototype（画面） |
| `data/persona.md` `data/sources/` | 家通の口調と史実の資料 | engine/（会話エンジン、史実） |
| `data/spots.json` | スポット表 | spots/（おすすめスポット） |

`data/` はコピー。担当が `engine/persona.md`、`engine/sources/`、`spots/spots.json` を直したら、デプロイ前に `python sync_data.py` を1回実行する。

## 決めた値（環境変数で変えられる）

| 環境変数 | 既定値 | 意味 |
| --- | --- | --- |
| `OPENAI_API_KEY` | なし | ないときは「試験モード」（固定の返事）で動く |
| `OPENAI_MODEL` | `gpt-6-luna` | |
| `REASONING_EFFORT` | `medium` | トークン実測と同じ値 |
| `TURN_LIMIT` | `10` | 1人の会話回数。あいさつは数えない |
| `DAILY_TOKEN_LIMIT` | `6000000` | 全体の1日の上限（トークン）。10往復で約2.7万なので約220人分 |
| `DATABASE_URL` | なし | Supabase の Transaction pooler（6543）。なければ `local_data/app.db`（SQLite） |
| `ADMIN_TOKEN` | なし | CSV書き出し用。英数字の長い文字列 |

- 資料とスポット表は毎回指示文に入れる（10/29の比較でキャッシュが効き、判定ありより安かった）。
- 自由入力の本文はログに残さない。turns には「自由入力かボタンか」と文字数だけ残す。
- 会話の履歴はメモリに持つ。30分操作がないと消える。

## 手元で動かす

```
pip install -r requirements.txt
# OPENAI_API_KEY を .env に書く（書かなければ試験モード）
uvicorn main:app --port 8000
```

ブラウザで http://localhost:8000 を開く。ブースPCは http://localhost:8000/?device=pc 。

## Cloud Run に出す（PowerShell）

`ops/min-deploy/作成後の手順_Windows.md` と同じ流れ。このフォルダ（app）に `cd` してから打つ。

```powershell
python sync_data.py
gcloud run deploy togashi-app --source . `
  --region asia-northeast1 `
  --allow-unauthenticated `
  --max-instances 1 `
  --set-env-vars "OPENAI_API_KEY=sk-...,ADMIN_TOKEN=英数字の長い文字列,DATABASE_URL=postgresql://postgres.xxxxxxxx:パスワード@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"
```

- `--max-instances 1` は必須（2台になると会話が session_not_found になる）。
- まつり当日だけ `--min-instances 1`（約$0.3/日）、終わったら0に戻す。
- 表は最初の起動で自動で作られる。Supabase の Table Editor に4表が出ていればよい。
- キーと接続文字列は、本番前に Secret Manager へ移す（ログ・運用の次回タスク）。

## 確認すること

1. `https://<URL>/healthz` が `"fake_ai": false` を返す（true ならキーが入っていない）。
2. スマホでQRから開き、おすすめ → 気分 → カード → 詳しく聞く、歴史 → 話題、自由入力、おわる → アンケート まで通す。
3. 返事が少しずつ表示され、右上の「残り◯回」が減る。0回で別れのあいさつとアンケートが出る。
4. `https://<URL>/admin/export/turns.csv?token=<ADMIN_TOKEN>` でログが取れる（sessions / surveys / daily_usage も同じ形）。

2026-11-02 に手元で確認済み: 試験モードで上の2〜4（SQLite と Postgres の両方）、AIが失敗したときの定型文、幅375pxで横にはみ出さないこと。本物のAIでの動作とCloud Runへのデプロイはまだ。
