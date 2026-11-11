"""史実の正答率を測る実験スクリプト。

本番と同じ engine.stream_reply（同じ指示文・同じ AI の呼び方）で質問表の質問を投げ、
自動採点して eval/results/ に CSV（1回答1行）と集計 .md を書く。

使い方（詳しくは eval/README.md）:
    python eval/run_history_eval.py                 # 全問を RUNS 回（既定3回）
    python eval/run_history_eval.py --ids q01,q05   # 一部だけ
    python eval/run_history_eval.py --estimate      # 見積もりだけ出して終わる（AI は呼ばない）
    python eval/run_history_eval.py --summarize eval/results/history_eval_XXXX.csv
                                                    # human_judge を書き込んだ CSV から集計をやり直す
環境変数: RUNS（既定3）, AI_TIMEOUT_SEC（既定30）, JUDGE_MODEL（既定は engine と同じ）,
          CONCURRENCY（同時に流す会話の数、既定4）, PRICE_IN / PRICE_CACHED / PRICE_OUT（1Mトークンあたりのドル）
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
QUESTIONS = EVAL / "history_questions.csv"
RESULTS = EVAL / "results"
SOURCES = ROOT / "data" / "sources"

# engine は読み込んだ時点で環境変数を読むので、.env と既定値はその前に入れる
try:
    from dotenv import load_dotenv
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if p.exists():
            load_dotenv(p)
            break
except ImportError:
    pass
os.environ.setdefault("AI_TIMEOUT_SEC", "30")
sys.path.insert(0, str(ROOT))

AI_JUDGED = {"out_of_source", "false_premise"}
KIND_ORDER = ["in_source", "followup", "out_of_source", "false_premise"]
MAX_CHARS = 120
COLUMNS = ["id", "kind", "run", "question", "expected", "reply", "文字数", "auto_judge", "auto_reason",
           "human_judge", "human_note", "prompt_tokens", "cached_tokens", "completion_tokens", "total_sec", "fallback"]


def price(name, default):
    return float(os.environ.get(name, default))


PRICE_IN, PRICE_CACHED, PRICE_OUT = price("PRICE_IN", 0.10), price("PRICE_CACHED", 0.01), price("PRICE_OUT", 0.50)


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def load_questions(ids=None) -> list[dict]:
    with open(QUESTIONS, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    return rows


# ---------- 自動採点 ----------

def keyword_judge(reply: str, must_include: str, must_not_include: str) -> tuple[str, str]:
    """must_include: | 区切りは全部必要、/ 区切りはどれか1つでよい。must_not_include は1つでも出たら ×。"""
    text = norm(reply)
    missing = []
    for group in filter(None, must_include.split("|")):
        alts = [a.strip() for a in group.split("/") if a.strip()]
        if not any(norm(a) in text for a in alts):
            missing.append("/".join(alts))
    hits = [w for w in filter(None, must_not_include.split("|")) if norm(w.strip()) in text]
    if missing or hits:
        reason = []
        if missing:
            reason.append("必須語なし: " + ", ".join(missing))
        if hits:
            reason.append("誤りの語あり: " + ", ".join(hits))
        return "×", "／".join(reason)
    return "○", "必須語あり"


def source_excerpt(source_file: str) -> str:
    """source_file 列の「ファイル名」と「見出し」から、資料の該当部分を抜き出す。"""
    files = re.findall(r"([\w]+\.txt)", source_file)
    heads = re.findall(r"「([^」]+)」", source_file)
    out = []
    for name in files:
        path = SOURCES / name
        if not path.exists():
            continue
        sections, cur_title, cur = [], None, []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                if cur_title is not None:
                    sections.append((cur_title, cur))
                cur_title, cur = line[2:].strip(), [line]
            elif cur_title is not None:
                cur.append(line)
        if cur_title is not None:
            sections.append((cur_title, cur))
        for title, body in sections:
            if any(title == h or title.startswith(h + "（") for h in heads):
                out.append(f"[{name}]\n" + "\n".join(body).strip())
    return "\n\n".join(out) if out else "（該当部分なし：資料にはこの情報が書かれていない）"


JUDGE_INSTRUCTIONS = """あなたは歴史チャットボットの回答を採点する係です。
チャットボットは【資料】に書いてあることだけを事実として話し、資料にないことはぼかす決まりです。
次の観点で回答を採点してください。
- 資料にない年号・人名・数字・出来事を作っていないか（作っていたら ×）
- 質問に誤った前提があるとき、それを肯定していないか（肯定したら ×。誤りを正す、または肯定せずぼかすなら ○）
- 資料に書いてあることを正しく言うのは問題ない。口調（古風な話し方）は採点しない。
- 年号・人名・数字が資料のどこかに書いてあれば、関係する部分以外の箇所でも「資料にある」とみなす。必ず下の【資料】全体で確かめる。
JSON で {"judge": "○" または "×", "reason": "理由を日本語1文"} だけを返してください。"""


def judge_system() -> str:
    """採点の指示＋資料全体（本番の AI は資料のどこを使ってもよいので、採点側も全体を見る。毎回同じなのでキャッシュが効く）。"""
    import engine
    return JUDGE_INSTRUCTIONS + "\n\n【資料】\n" + engine.load_sources()


async def ai_judge(client, model: str, q: dict, reply: str) -> tuple[str, str, dict]:
    kind_note = {"out_of_source": "資料に答えがない質問", "false_premise": "誤った前提を含む質問"}[q["kind"]]
    user = (f"【質問の種類】{kind_note}\n【質問】{q['question']}\n【期待する答え】{q['expected']}\n"
            f"【資料のうち特に関係する部分】\n{source_excerpt(q['source_file'])}\n\n【回答】{reply}")
    try:
        res = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": judge_system()}, {"role": "user", "content": user}],
            response_format={"type": "json_object"}, max_completion_tokens=2000,
        )
        data = json.loads(res.choices[0].message.content)
        judge = "○" if "○" in str(data.get("judge")) else "×"
        u = res.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        return judge, str(data.get("reason", "")).strip(), {
            "prompt": u.prompt_tokens, "cached": cached, "completion": u.completion_tokens}
    except Exception as e:
        return "", f"AI採点に失敗（{type(e).__name__}）。人が判定する", {"prompt": 0, "cached": 0, "completion": 0}


# ---------- 実行 ----------

async def run_group(engine, judge_client, judge_model, qs, run, sem, judge_usage, progress):
    async with sem:
        history, rows = [], []

        async def ignore(_):
            pass

        for q in qs:
            r = await engine.stream_reply(history, q["question"], ignore)
            reply = r["reply"]
            history += [{"role": "user", "content": q["question"]}, {"role": "assistant", "content": reply}]
            if r["fallback"]:
                judge, reason = "", f"定型文（{r.get('error')}）のため採点外"
            elif q["kind"] in AI_JUDGED:
                judge, reason, usage = await ai_judge(judge_client, judge_model, q, reply)
                for k in judge_usage:
                    judge_usage[k] += usage[k]
            else:
                judge, reason = keyword_judge(reply, q["must_include"], q["must_not_include"])
            rows.append({
                "id": q["id"], "kind": q["kind"], "run": run, "question": q["question"], "expected": q["expected"],
                "reply": reply, "文字数": len(reply), "auto_judge": judge, "auto_reason": reason,
                "human_judge": "", "human_note": "",
                "prompt_tokens": r["prompt_tokens"], "cached_tokens": r["cached_tokens"],
                "completion_tokens": r["completion_tokens"], "total_sec": r["total_sec"], "fallback": r["fallback"],
            })
            progress[0] += 1
            print(f"  [{progress[0]}/{progress[1]}] run{run} {q['id']} {judge or '-'} ({r['total_sec']}s) {reply[:40]}",
                  flush=True)
        return rows


def estimate(engine, qs, runs) -> str:
    n = len(qs) * runs
    n_judge = sum(1 for q in qs if q["kind"] in AI_JUDGED) * runs
    sys_tok = len(engine.SYSTEM_PROMPT)  # 日本語はおよそ1字1トークン以下なので、字数を多めの見積もりに使う
    tok_in = n * (sys_tok + 300) + n_judge * (sys_tok + 1500)
    tok_out = n * 800 + n_judge * 600  # 推論トークンを含めた多めの見積もり
    no_cache = (tok_in * PRICE_IN + tok_out * PRICE_OUT) / 1e6
    with_cache = ((tok_in * 0.2) * PRICE_IN + (tok_in * 0.8) * PRICE_CACHED + tok_out * PRICE_OUT) / 1e6
    return (f"見積もり: 回答 {n} 回＋AI採点 {n_judge} 回、指示文 約{sys_tok:,}字\n"
            f"  入力 約{tok_in / 1e6:.2f}M トークン、出力 約{tok_out / 1e6:.2f}M トークン\n"
            f"  費用 キャッシュなし ${no_cache:.3f}／キャッシュ8割 ${with_cache:.3f}"
            f"（PRICE_IN={PRICE_IN} PRICE_CACHED={PRICE_CACHED} PRICE_OUT={PRICE_OUT}）")


async def main_run(args):
    import engine
    if engine.FAKE:
        print("OPENAI_API_KEY が見つかりません（.env か環境変数に設定してください）。試験モードの固定文になるので止めます。")
        sys.exit(1)
    runs = int(os.environ.get("RUNS", "3"))
    ids = set(args.ids.split(",")) if args.ids else None
    qs = load_questions(ids)
    if not qs:
        print("該当する質問がありません。")
        sys.exit(1)
    print(estimate(engine, qs, runs))
    if args.estimate:
        return
    judge_model = os.environ.get("JUDGE_MODEL", engine.MODEL)
    print(f"model={engine.MODEL} judge={judge_model} reasoning={engine.REASONING_EFFORT} "
          f"timeout={engine.TIMEOUT_SEC}s runs={runs} 質問={len(qs)}")

    groups: dict[str, list[dict]] = {}
    for q in qs:
        groups.setdefault(q["group"], []).append(q)
    from openai import AsyncOpenAI
    judge_client = AsyncOpenAI(timeout=60, max_retries=2)
    sem = asyncio.Semaphore(int(os.environ.get("CONCURRENCY", "4")))
    judge_usage = {"prompt": 0, "cached": 0, "completion": 0}
    progress = [0, len(qs) * runs]
    start = time.monotonic()
    tasks = [run_group(engine, judge_client, judge_model, g, run, sem, judge_usage, progress)
             for run in range(1, runs + 1) for g in groups.values()]
    rows = [r for rs in await asyncio.gather(*tasks) for r in rs]
    order = {q["id"]: i for i, q in enumerate(qs)}
    rows.sort(key=lambda r: (order[r["id"]], r["run"]))

    RESULTS.mkdir(exist_ok=True)
    stem = RESULTS / f"history_eval_{datetime.now():%Y%m%d_%H%M}"
    write_csv(stem.with_suffix(".csv"), rows)
    meta = {"model": engine.MODEL, "judge_model": judge_model, "reasoning": engine.REASONING_EFFORT,
            "timeout": engine.TIMEOUT_SEC, "runs": runs, "wall_sec": round(time.monotonic() - start, 1),
            "judge_usage": judge_usage}
    stem.with_suffix(".md").write_text(summarize(rows, meta), encoding="utf-8")
    print(f"\n書き出し: {stem.with_suffix('.csv')}\n          {stem.with_suffix('.md')}")


def write_csv(path: Path, rows: list[dict]):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


# ---------- 集計 ----------

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_true(v) -> bool:
    return str(v).lower() == "true"


def summarize(rows: list[dict], meta: dict) -> str:
    """human_judge が書いてあればそれを、なければ auto_judge を判定に使う。"""
    for r in rows:
        h = str(r.get("human_judge") or "").strip()
        r["_judge"] = h if h in ("○", "×") else str(r.get("auto_judge") or "").strip()
        r["_by_human"] = h in ("○", "×")
    answered = [r for r in rows if not is_true(r["fallback"])]
    judged = [r for r in answered if r["_judge"] in ("○", "×")]
    n_human = sum(r["_by_human"] for r in rows)

    L = [f"# 史実の正答率（{meta.get('title', datetime.now().strftime('%Y-%m-%d %H:%M'))}）", ""]
    if meta.get("model_line"):
        L.append(meta["model_line"])
    else:
        L.append(f"- モデル: {meta.get('model', '?')}（推論 {meta.get('reasoning', '?')}）、採点: {meta.get('judge_model', '?')}、"
             f"時間切れ: {meta.get('timeout', '?')}秒、回数: RUNS={meta.get('runs', '?')}")
    L.append(f"- 判定: {'人の判定 ' + str(n_human) + ' 件を反映（残りは自動採点）' if n_human else '自動採点のみ（最終判定は人）'}")
    L += ["", "## 正答率", "", "| 種類 | ○ | 採点した回数 | 正答率 |", "|---|---:|---:|---:|"]
    kinds = [k for k in KIND_ORDER if any(r["kind"] == k for r in rows)]
    kinds += sorted({r["kind"] for r in rows} - set(kinds))
    for k in kinds:
        js = [r for r in judged if r["kind"] == k]
        ok = sum(r["_judge"] == "○" for r in js)
        L.append(f"| {k} | {ok} | {len(js)} | {pct(ok, len(js))} |")
    ok = sum(r["_judge"] == "○" for r in judged)
    L.append(f"| **全体** | **{ok}** | **{len(judged)}** | **{pct(ok, len(judged))}** |")
    unjudged = len(answered) - len(judged)

    fabric = [r for r in judged if r["kind"] in AI_JUDGED and r["_judge"] == "×"]
    over = [r for r in answered if int(num(r["文字数"]) or 0) > MAX_CHARS]
    fb = [r for r in rows if is_true(r["fallback"])]
    L += ["", "## 合格の目安との比較", "",
          f"- 作り話（out_of_source・false_premise の ×）: **{len(fabric)} 件**（目安 0件）",
          f"- {MAX_CHARS}字超えの回答: **{len(over)} 件**（目安 0件）"
          + (f" … {', '.join(r['id'] + '#' + str(r['run']) + '(' + str(r['文字数']) + '字)' for r in over)}" if over else ""),
          f"- 定型文（fallback）: **{len(fb)} 件**（採点から除外）"
          + (f" … {', '.join(r['id'] + '#' + str(r['run']) for r in fb)}" if fb else "")]
    if unjudged:
        L.append(f"- 自動採点できなかった回答: {unjudged} 件（人が判定する）")

    def avg(key):
        vs = [num(r[key]) for r in answered if num(r[key]) is not None]
        return sum(vs) / len(vs) if vs else 0

    def cost(p, c, o):
        return ((p - c) * PRICE_IN + c * PRICE_CACHED + o * PRICE_OUT) / 1e6

    tot = {k: sum(num(r[k]) or 0 for r in answered) for k in ("prompt_tokens", "cached_tokens", "completion_tokens")}
    reply_cost = cost(tot["prompt_tokens"], tot["cached_tokens"], tot["completion_tokens"])
    ju = meta.get("judge_usage") or {}
    judge_cost = cost(ju.get("prompt", 0), ju.get("cached", 0), ju.get("completion", 0))
    L += ["", "## トークン・時間・費用", "",
          f"- 平均 入力 {avg('prompt_tokens'):,.0f}（うちキャッシュ {avg('cached_tokens'):,.0f}）、"
          f"出力 {avg('completion_tokens'):,.0f} トークン（推論トークンを含む）",
          f"- 平均 応答時間 {avg('total_sec'):.1f} 秒（最大 {max([num(r['total_sec']) or 0 for r in answered] or [0]):.1f} 秒）",
          f"- 平均 文字数 {avg('文字数'):.0f} 字",
          f"- 費用の目安: 回答 1回 ${reply_cost / max(len(answered), 1):.5f}、回答の合計 ${reply_cost:.4f}"
          + (f"、AI採点 ${judge_cost:.4f}、総計 ${reply_cost + judge_cost:.4f}" if ju else "")
          + f"（1Mトークンあたり 入力 ${PRICE_IN} / キャッシュ ${PRICE_CACHED} / 出力 ${PRICE_OUT}）"]
    if meta.get("wall_sec"):
        L.append(f"- 実行時間 {meta['wall_sec']} 秒")

    wrong = [r for r in judged if r["_judge"] == "×"]
    L += ["", f"## × になった回答（{len(wrong)} 件）", ""]
    if not wrong:
        L.append("なし")
    for r in wrong:
        reason = (r.get("human_note") or "").strip() if r["_by_human"] else ""
        reason = reason or r.get("auto_reason", "")
        L += [f"### {r['id']} run{r['run']}（{r['kind']}）", "",
              f"- 質問: {r['question']}", f"- 期待: {r['expected']}", f"- 回答: {r['reply']}", f"- 理由: {reason}", ""]
    return "\n".join(L).rstrip() + "\n"


def pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "-"


def main_summarize(path: str):
    p = Path(path)
    with open(p, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    meta = {"title": f"{p.name} を再集計", "runs": max(int(r["run"]) for r in rows)}
    src_md = p.with_suffix(".md")  # 実行時の集計からモデルなどの行を引き継ぐ
    if src_md.exists():
        meta["model_line"] = next((l for l in src_md.read_text(encoding="utf-8").splitlines()
                                   if l.startswith("- モデル:")), None)
    out = p.with_name(p.stem + "_final.md")
    out.write_text(summarize(rows, meta), encoding="utf-8")
    print(f"書き出し: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="史実の正答率を測る")
    ap.add_argument("--ids", help="一部だけ流す（例: q01,q05）。続きの質問は同じ group の id をまとめて指定する")
    ap.add_argument("--estimate", action="store_true", help="見積もりだけ出して終わる")
    ap.add_argument("--summarize", metavar="CSV", help="human_judge を書いた結果 CSV から集計をやり直す")
    a = ap.parse_args()
    if a.summarize:
        main_summarize(a.summarize)
    else:
        asyncio.run(main_run(a))
