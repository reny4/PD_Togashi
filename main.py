"""富樫家通チャットボット（試作完成版）のサーバ。画面（static/）とAPIを同じURLで出す。

API は会話API仕様 v0.1（https://claude.ai/artifact/KQUBZWKMvZ4MaRovxjKyUz）のとおり。
- POST /api/session          体験の開始
- POST /api/chat             1往復。?stream=1 を付けると SSE（delta を少しずつ、最後に done で通常のJSONと同じ中身）
- POST /api/survey           アプリ内アンケート
- GET  /admin/export/{表}.csv?token=...  ログのCSV（sessions / turns / surveys / daily_usage）
- GET  /healthz              動作確認

会話の状態はメモリに持つので、Cloud Run は --max-instances 1 で動かす。
"""
import asyncio
import csv
import io
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()  # 手元では .env の OPENAI_API_KEY などを読む（Cloud Run では環境変数）

import db  # noqa: E402
import engine  # noqa: E402
import flow  # noqa: E402

JST = timezone(timedelta(hours=9))
TURN_LIMIT = int(os.environ.get("TURN_LIMIT", "10"))
DAILY_TOKEN_LIMIT = int(os.environ.get("DAILY_TOKEN_LIMIT", "6000000"))
SESSION_TTL_SEC = 30 * 60
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
SURVEY_KEYS = {"want_to_know_teach_me", "ez_to_use", "want_to_know_more", "want_to_visit"}

app = FastAPI()
SESSIONS: dict[str, dict] = {}


def now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(JST).date().isoformat()


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def log(fn, *args):
    """ログが書けなくても体験は止めない。"""
    try:
        await run_in_threadpool(fn, *args)
    except Exception as e:
        print(f"[log error] {fn.__name__}: {type(e).__name__}: {e}")


@app.on_event("startup")
def startup():
    db.init()
    print(f"engine: {'試験モード（OPENAI_API_KEY なし）' if engine.FAKE else engine.MODEL}, "
          f"log: {'postgres' if db.DATABASE_URL else db.DB_PATH}, TURN_LIMIT={TURN_LIMIT}")


@app.get("/healthz")
def healthz():
    return {"ok": True, "fake_ai": engine.FAKE, "sessions": len(SESSIONS)}


# ---------- 1. 体験の開始 ----------

@app.post("/api/session")
async def start_session(request: Request):
    body = await _json(request)
    device = "pc" if body.get("device") == "pc" else "phone"
    t = time.time()
    for sid in [k for k, s in SESSIONS.items() if t - s["last_at"] > SESSION_TTL_SEC]:
        del SESSIONS[sid]
    sid = "s_" + secrets.token_hex(6)
    SESSIONS[sid] = {"id": sid, "device": device, "history": [], "turns_left": TURN_LIMIT, "turns_used": 0,
                     "ended": False, "last_at": t, "asked": set(), "seen": False, "topic": "other"}
    await log(db.add_session, sid, now(), device)
    return {"session_id": sid, "reply": flow.GREETING, "choices": flow.START_CHOICES, "spots": [],
            "turns_left": TURN_LIMIT, "fallback": False, "ended": False}


# ---------- 2. 会話 ----------

@app.post("/api/chat")
async def chat(request: Request, stream: int = 0):
    body = await _json(request)
    sess = SESSIONS.get(body.get("session_id") or "")
    if not sess or time.time() - sess["last_at"] > SESSION_TTL_SEC:
        return error(404, "session_not_found", "IDがない・期限切れ・サーバ再起動")
    message, choice_id = body.get("message"), body.get("choice_id")
    if (message is None) == (choice_id is None):
        return error(400, "bad_request", "message と choice_id はどちらか1つだけ")
    if message is not None and not (isinstance(message, str) and 1 <= len(message.strip()) <= 100):
        return error(400, "bad_request", "message は1〜100字")
    if choice_id is not None and not flow.is_known_choice(str(choice_id), {s["id"]: s for s in engine.SPOTS}):
        return error(400, "bad_request", f"知らない choice_id: {choice_id}")
    sess["last_at"] = time.time()

    if not stream:
        return await run_turn(sess, choice_id, message.strip() if message else None, None)

    queue: asyncio.Queue = asyncio.Queue()

    async def on_text(t):
        await queue.put(("delta", {"text": t}))

    async def worker():
        try:
            result = await run_turn(sess, choice_id, message.strip() if message else None, on_text)
        except Exception as e:  # 想定外でも画面は止めない
            print(f"[turn error] {type(e).__name__}: {e}")
            result = {"reply": engine.FALLBACK_REPLY, "choices": [], "spots": [],
                      "turns_left": sess["turns_left"], "fallback": True, "ended": sess["ended"]}
        await queue.put(("done", result))

    async def gen():
        task = asyncio.create_task(worker())
        while True:
            event, data = await queue.get()
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if event == "done":
                break
        await task

    # X-Accel-Buffering: 途中のプロキシにまとめ送りさせないため
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def run_turn(sess: dict, choice_id, message, on_text) -> dict:
    """1往復を処理して、/api/chat の返す形（dict）を作る。on_text があれば少しずつ文字を渡す。"""
    async def emit(t):
        if on_text:
            await on_text(t)

    # 終わった体験にまた送った: 別れの定型文、回数は減らない
    if sess["ended"]:
        await emit(flow.FAREWELL)
        return _payload(sess, flow.FAREWELL, [], [], False)

    if choice_id == "end":
        sess["ended"] = True
        sess["turns_used"] += 1
        sess["turns_left"] = max(sess["turns_left"] - 1, 0)
        await emit(flow.FAREWELL)
        await log(db.add_turn, _turn_row(sess, choice_id, None, "other", {}))
        await log(db.end_session, sess["id"], now(), "end_button", sess["turns_used"])
        return _payload(sess, flow.FAREWELL, [], [], False)

    p = flow.plan(sess, choice_id, message, engine.SPOTS)
    sess["topic"] = p["topic"]
    sess["turns_used"] += 1
    sess["turns_left"] = max(sess["turns_left"] - 1, 0)
    metrics: dict = {}
    fallback = False
    spots: list = []

    if p["kind"] == "fixed":
        reply, spots = p["reply"], p["spots"]
        await emit(reply)
    elif await run_in_threadpool(_over_daily_limit):
        reply, fallback = flow.DAILY_LIMIT_REPLY, True
        sess["ended"] = True
        metrics = {"error": "daily_limit"}
        await emit(reply)
    else:
        metrics = await engine.stream_reply(sess["history"], p["question"], emit)
        reply, fallback = metrics["reply"], metrics["fallback"]
        if not fallback:
            sess["history"] += [{"role": "user", "content": p["question"]}, {"role": "assistant", "content": reply}]
            used = (metrics["prompt_tokens"] or 0) + (metrics["completion_tokens"] or 0)
            await log(db.add_usage, today(), used)

    choices = p["choices"]
    reason = "daily_limit" if sess["ended"] else None
    if not sess["ended"] and sess["turns_left"] <= 0:
        reply += "\n\n" + flow.LIMIT_FAREWELL
        await emit("\n\n" + flow.LIMIT_FAREWELL)
        sess["ended"], reason = True, "turn_limit"

    await log(db.add_turn, _turn_row(sess, choice_id, message, p["topic"], metrics, fallback))
    if sess["ended"]:
        await log(db.end_session, sess["id"], now(), reason, sess["turns_used"])
    return _payload(sess, reply, [] if sess["ended"] else choices, spots, fallback)


def _over_daily_limit() -> bool:
    try:
        return db.tokens_used(today()) >= DAILY_TOKEN_LIMIT
    except Exception as e:
        print(f"[log error] tokens_used: {e}")
        return False


def _payload(sess, reply, choices, spots, fallback) -> dict:
    return {"reply": reply, "choices": choices, "spots": spots, "turns_left": sess["turns_left"],
            "fallback": fallback, "ended": sess["ended"]}


def _turn_row(sess, choice_id, message, topic, m: dict, fallback=False) -> dict:
    # 自由入力の本文は残さない（個人情報を取らないため）。種類と文字数だけ
    return {
        "session_id": sess["id"], "turn_no": sess["turns_used"], "at": now(),
        "input_type": "text" if message is not None else "choice", "choice_id": choice_id,
        "input_chars": len(message) if message is not None else None, "topic": topic,
        "fallback": 1 if fallback else 0, "error": m.get("error"),
        "prompt_tokens": m.get("prompt_tokens"), "cached_tokens": m.get("cached_tokens"),
        "completion_tokens": m.get("completion_tokens"), "first_token_sec": m.get("first_token_sec"),
        "total_sec": m.get("total_sec"),
    }


# ---------- 3. アンケート ----------

@app.post("/api/survey")
async def survey(request: Request):
    body = await _json(request)
    sid = body.get("session_id") or ""
    if sid not in SESSIONS:
        return error(404, "session_not_found", "IDがない・期限切れ・サーバ再起動")
    skipped = bool(body.get("skipped", False))
    answers = {}
    for a in ([] if skipped else body.get("answers") or []):
        q, v = a.get("q"), a.get("value")
        if q not in SURVEY_KEYS or not isinstance(v, int) or not 1 <= v <= 5:
            return error(400, "bad_request", f"知らない q か value が1〜5以外: {a}")
        answers[q] = v
    await log(db.add_survey, sid, now(), skipped, answers)
    return {"ok": True}


# ---------- ログのCSV ----------

@app.get("/admin/export/{name}.csv")
async def export_csv(name: str, token: str = ""):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return error(403, "forbidden", "token が違う")
    if name not in db.TABLES:
        return error(404, "not_found", "表の名前は sessions / turns / surveys / daily_usage")
    cols, rows = await run_in_threadpool(db.export, name)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    # utf-8-sig: Excelで開いても文字化けしないように
    return StreamingResponse(iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={name}_{today()}.csv"})


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# 画面（最後に置く。/api より後）
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
