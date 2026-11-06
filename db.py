"""ログの保存先。DATABASE_URL があれば Postgres（Supabase）、なければ SQLite（手元の確認用）。

表は4つ（ログ・運用担当の設計）。起動時に無ければ作る。
- sessions   : 1体験1行
- turns      : 1往復1行。自由入力の本文は残さない（入力の種類と文字数だけ）
- surveys    : アプリ内アンケート。答えなかった問は NULL、「答えない」は skipped=1
- daily_usage: 全体の1日の使用量（トークン数）。1日上限の判定に使う
"""
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "")  # Supabase の Transaction pooler（ポート6543）
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./local_data"), "app.db")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, started_at TEXT, device TEXT,
        ended_at TEXT, end_reason TEXT, turns_used INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS turns (
        session_id TEXT, turn_no INTEGER, at TEXT, input_type TEXT, choice_id TEXT,
        input_chars INTEGER, topic TEXT, fallback INTEGER, error TEXT,
        prompt_tokens INTEGER, cached_tokens INTEGER, completion_tokens INTEGER,
        first_token_sec REAL, total_sec REAL,
        PRIMARY KEY (session_id, turn_no))""",
    """CREATE TABLE IF NOT EXISTS surveys (
        session_id TEXT PRIMARY KEY, at TEXT, skipped INTEGER,
        want_to_know_teach_me INTEGER, ez_to_use INTEGER, want_to_know_more INTEGER, want_to_visit INTEGER)""",
    """CREATE TABLE IF NOT EXISTS daily_usage (
        day TEXT PRIMARY KEY, tokens INTEGER DEFAULT 0, turns INTEGER DEFAULT 0)""",
]

TABLES = {
    "sessions": ["session_id", "started_at", "device", "ended_at", "end_reason", "turns_used"],
    "turns": ["session_id", "turn_no", "at", "input_type", "choice_id", "input_chars", "topic", "fallback", "error",
              "prompt_tokens", "cached_tokens", "completion_tokens", "first_token_sec", "total_sec"],
    "surveys": ["session_id", "at", "skipped", "want_to_know_teach_me", "ez_to_use", "want_to_know_more", "want_to_visit"],
    "daily_usage": ["day", "tokens", "turns"],
}


def query(sql: str, params: tuple = ()) -> list:
    """SQLは ? で書く。Postgres のときは %s に置き換える。"""
    if DATABASE_URL:
        import psycopg
        # Transaction pooler はプリペアドステートメント非対応なので切る
        with psycopg.connect(DATABASE_URL, prepare_threshold=None) as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            return cur.fetchall() if cur.description else []
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()


def init() -> None:
    for sql in SCHEMA:
        query(sql)


def add_session(session_id, started_at, device):
    query("INSERT INTO sessions (session_id, started_at, device) VALUES (?, ?, ?)", (session_id, started_at, device))


def end_session(session_id, ended_at, reason, turns_used):
    query("UPDATE sessions SET ended_at = ?, end_reason = ?, turns_used = ? WHERE session_id = ?",
          (ended_at, reason, turns_used, session_id))


def add_turn(row: dict):
    cols = TABLES["turns"]
    query(f"INSERT INTO turns ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
          tuple(row.get(c) for c in cols))


def add_usage(day: str, tokens: int):
    # DB側で足す（同時に書いても数がずれない）
    query("INSERT INTO daily_usage (day, tokens, turns) VALUES (?, ?, 1) "
          "ON CONFLICT (day) DO UPDATE SET tokens = daily_usage.tokens + excluded.tokens, turns = daily_usage.turns + 1",
          (day, tokens))


def tokens_used(day: str) -> int:
    rows = query("SELECT tokens FROM daily_usage WHERE day = ?", (day,))
    return rows[0][0] if rows else 0


def add_survey(session_id, at, skipped, answers: dict):
    # 同じ session_id の2回目は記録しない（連打対策）
    query("INSERT INTO surveys (session_id, at, skipped, want_to_know_teach_me, ez_to_use, want_to_know_more, want_to_visit) "
          "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (session_id) DO NOTHING",
          (session_id, at, 1 if skipped else 0, answers.get("want_to_know_teach_me"), answers.get("ez_to_use"),
           answers.get("want_to_know_more"), answers.get("want_to_visit")))


def export(table: str) -> tuple[list[str], list]:
    cols = TABLES[table]
    return cols, query(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {cols[0]}")
