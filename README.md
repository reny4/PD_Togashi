# OpenAI API 体験サンプル

OpenAI API (Chat Completions) を Python から呼び出して、AI の返答を表示するだけのシンプルなサンプルです。
`main.py` が「Pythonとは何ですか？」と質問し、`gpt-4o-mini` の回答をターミナルに出力します。

## 必要なもの

- Windows
- Python 3.9 以上
- Git
- OpenAI の API キー（配布者から個別に共有されます）
  - **API キーは他の人に教えたり、SNS・チャット・Git などに載せたりしないでください。**

## 手順

以下は PowerShell で実行します。

### 1. クローン

```powershell
git clone https://github.com/reny4/PD_Togashi.git
cd PD_Togashi
```

### 2. 仮想環境の作成（推奨）

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> スクリプト実行が拒否される場合は、先に
> `Set-ExecutionPolicy -Scope Process RemoteSigned` を実行してください。

### 3. ライブラリのインストール

```powershell
pip install openai python-dotenv
```

### 4. API キーの設定

共有された API キーを `.env` ファイルに設定します。`.env.example` をコピーして `.env` を作成してください。

```powershell
Copy-Item .env.example .env
```

`.env` をメモ帳や VS Code で開き、共有されたキーを貼り付けて保存します。

```
OPENAI_API_KEY=共有されたAPIキー
```

- `=` の前後にスペースや引用符（`"`）は付けないでください。
- `.env` は `.gitignore` に含まれているため、Git にはコミットされません。

### 5. 実行

```powershell
python main.py
```

AI からの「Pythonとは何ですか？」への回答が表示されれば成功です。

## 体験してみよう

`main.py` を編集して挙動を変えられます。

| 変更箇所 | 例 |
| --- | --- |
| `"role": "user"` の `content` | 質問内容を好きな文に変える |
| `"role": "system"` の `content` | 「関西弁で答えて」など AI の口調・役割を変える |
| `model` | 使用するモデルを変える |

## トラブルシューティング

| 症状 | 原因と対処 |
| --- | --- |
| `ModuleNotFoundError: No module named 'openai'` / `'dotenv'` | 手順3が未実施、または仮想環境が有効化されていません |
| `OpenAIError: The api_key client option must be set...` | `.env` が無い、またはファイル名・キー名 (`OPENAI_API_KEY`) が違います。`main.py` と同じフォルダに置いてください |
| `AuthenticationError (401)` | API キーが間違っています。コピー時に余分な文字や空白が入っていないか確認してください |
| `RateLimitError (429)` | 利用上限に達した可能性があります。配布者に連絡してください |
| `python` が見つからない | `py main.py` を試してください |
