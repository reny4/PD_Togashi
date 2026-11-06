"""会話エンジン: 指示文を組み立てて AI を呼ぶ（engine/chat_cli.py の ask() を元にしたサーバ版）。

- 資料（data/sources）とスポット表（data/spots.json）は毎回指示文に入れる（ALWAYS_SOURCES=1 の結果より）。
  指示文の前半が毎回同じになり、OpenAI の自動キャッシュが効く。
- 返事は少しずつ返す（stream=True）。失敗しても止めず、定型文を返す（fallback）。
- OPENAI_API_KEY がないときは、固定の返事を返す試験モードで動く（画面やログの確認用）。
"""
import asyncio
import json
import os
import time
from pathlib import Path

DATA = Path(__file__).parent / "data"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
# 推論の深さ。トークン実測（2026-10-29）と同じ medium にそろえる
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "medium")
MAX_COMPLETION_TOKENS = int(os.environ.get("MAX_COMPLETION_TOKENS", "1000"))
TIMEOUT_SEC = float(os.environ.get("AI_TIMEOUT_SEC", "10"))
FALLBACK_REPLY = "すまぬ、少し考えがまとまらぬ。もう一度言うてくれぬか。"
FAKE = not os.environ.get("OPENAI_API_KEY")


def load_spots() -> list[dict]:
    return json.loads((DATA / "spots.json").read_text(encoding="utf-8"))


def load_sources() -> str:
    files = sorted((DATA / "sources").glob("*.txt")) + sorted((DATA / "sources").glob("*.md"))
    if not files:
        return "（なし）"
    parts = []
    for p in files:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        if lines and lines[0].startswith("出典"):
            lines = lines[1:]  # 1行目の出典URLは指示文に入れない（家通が読み上げないように）
        parts.append(f"## {p.stem}\n" + "\n".join(lines).strip())
    return "\n\n".join(parts)


def spots_for_prompt(spots: list[dict]) -> str:
    rows = []
    for s in spots:
        rows.append(
            f"- {s['name']}（{s['area']}、{'・'.join(s['tags'])}）: {s['card_description']}"
            f" 見どころ: {'、'.join(s.get('highlights', []))}。営業時間: {s.get('hours', '不明')}、休み: {s.get('closed_days', '不明')}"
        )
    return "\n".join(rows)


SPOTS = load_spots()
EXTRA_RULES = """
# おすすめスポットについて
- 場所や店を勧めるときは、下の【おすすめスポット】の中からだけ選ぶ。ここにない店や場所の名前は出さない。
- 営業時間や休みは【おすすめスポット】に書いてあることだけを言う。
"""
SYSTEM_PROMPT = (
    (DATA / "persona.md").read_text(encoding="utf-8").strip()
    + "\n" + EXTRA_RULES
    + f"\n【おすすめスポット】\n{spots_for_prompt(SPOTS)}"
    + f"\n\n【資料】\n{load_sources()}"
)

_client = None


def client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(timeout=TIMEOUT_SEC, max_retries=0)
    return _client


async def stream_reply(history: list[dict], user_text: str, on_text) -> dict:
    """AIに聞き、届いた文字を on_text(文字) に渡す。返事と計測値（ログ項目）を辞書で返す。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user_text}]
    start = time.monotonic()
    first = None
    if FAKE:
        text = f"（試験モード）「{user_text[:30]}」への家通の返事がここに入る。OPENAI_API_KEY を設定すると本物になる。"
        for ch in text:
            if first is None:
                first = time.monotonic() - start
            await on_text(ch)
            await asyncio.sleep(0.01)
        return _result(text, None, first, start)
    try:
        parts, usage = [], None
        res = await asyncio.wait_for(client().chat.completions.create(
            model=MODEL, messages=messages, max_completion_tokens=MAX_COMPLETION_TOKENS,
            reasoning_effort=REASONING_EFFORT, stream=True, stream_options={"include_usage": True},
        ), TIMEOUT_SEC)
        async for chunk in res:
            if chunk.usage:
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta.content:
                if first is None:
                    first = time.monotonic() - start
                parts.append(chunk.choices[0].delta.content)
                await on_text(chunk.choices[0].delta.content)
            if time.monotonic() - start > TIMEOUT_SEC * 3:
                raise TimeoutError("stream too long")
        reply = "".join(parts).strip()
        if not reply:
            raise ValueError("empty reply")
        return _result(reply, usage, first, start)
    except Exception as e:  # 失敗しても体験は止めない
        return {
            "reply": FALLBACK_REPLY, "fallback": True, "error": type(e).__name__,
            "prompt_tokens": None, "cached_tokens": None, "completion_tokens": None,
            "first_token_sec": None, "total_sec": round(time.monotonic() - start, 2),
        }


def _result(reply, usage, first, start) -> dict:
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    return {
        "reply": reply, "fallback": False, "error": None,
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "cached_tokens": cached if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "first_token_sec": round(first, 2) if first is not None else None,
        "total_sec": round(time.monotonic() - start, 2),
    }
