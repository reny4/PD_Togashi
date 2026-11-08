"""ボタンの流れ（画面ワークフロー 図2）。どのボタンでAIを呼ぶか、次にどのボタンを出すかを決める。

- おすすめ → 気分を3つから選ぶ → スポットのカード（最大3件、多ければランダム）。ここまでAIは呼ばない
- カードの「詳しく見る」→ 画面のモーダルを開くだけ（AIは呼ばない）。家通のひと言は data/ietsugu_comments.json
  （detail_ の choice_id は、モーダルから家通に話を聞く機能のために残している）
- 歴史 → 話題を選ぶ → AIが資料に沿って答える → まだ聞いていない話題を次のボタンに出す
- 自由入力 → AIが答える
文言を変えるときはこのファイルだけ直せばよい。
"""
import json
import random
from pathlib import Path

# スポットごとの家通のひと言（「詳しく見る」のモーダルに出す）。spots.json とは別ファイル（同期で上書きされないように）
_COMMENTS_FILE = Path(__file__).parent / "data" / "ietsugu_comments.json"
SPOT_COMMENTS = json.loads(_COMMENTS_FILE.read_text(encoding="utf-8")) if _COMMENTS_FILE.exists() else {}

GREETING = "我は富樫家通。この地のことや、今の世の面白きものを少し語れる者よ。気楽に声をかけてくれ。"
FAREWELL = "よう参ってくれた。またいつでも野々市で声をかけよ。"
LIMIT_FAREWELL = "おっと、今日はここまでじゃ。話せてうれしかったぞ。"
DAILY_LIMIT_REPLY = "すまぬ、今日はもう多くの者と話して疲れてしもうた。また明日来てくれ。"

# セリフ下のボタンは「その場の分岐」（気分・歴史の話題）だけ出す。
# 「おすすめを聞く」「歴史を聞く」は下の固定メニュー、「おわる」はヘッダーのボタンに一本化（2026-11-06）
START_CHOICES = []

# 気分メニュー（2026-10-29 renya決定）: id → (ボタンの文字, 対応するタグ)
MOODS = {
    "mood_history": ("歴史を知れる場所", {"歴史"}),
    "mood_rest": ("少し一休みできる場所", {"休憩", "落ち着く"}),
    "mood_food": ("おなかがすいた", {"食事"}),
}

# 歴史の話題: id → (ボタンの文字, AIに渡す質問)。史実担当の「次の話題の案」が届いたら差し替える
HISTORY_TOPICS = {
    "h_togashi": ("富樫氏について聞く", "富樫氏とはどんな一族だったのか、教えてほしい。"),
    "h_nonoichi": ("野々市の昔について聞く", "昔の野々市はどんな町だったのか、教えてほしい。"),
    "h_ikki": ("一向一揆について聞く", "加賀の一向一揆と富樫氏の関わりを教えてほしい。"),
    "h_edo": ("江戸時代の野々市を聞く", "江戸時代の野々市はどんな所だったのか、教えてほしい。"),
}


def is_known_choice(choice_id: str, spots_by_id: dict) -> bool:
    if choice_id in ("spot", "history", "end") or choice_id in MOODS or choice_id in HISTORY_TOPICS:
        return True
    return choice_id.startswith("detail_") and choice_id[len("detail_"):] in spots_by_id


_IMAGES_DIR = Path(__file__).parent / "static" / "images"


def photo_url(spot: dict) -> str:
    """spots.json の photo（ファイル名だけ。例 "spot_001.jpg"）を画面用のパスにする。
    未設定・ファイルがない・ファイル名以外が書かれているときは ""（画面は家紋の代わり絵を出す）"""
    name = (spot.get("photo") or "").strip()
    if not name or Path(name).name != name or not (_IMAGES_DIR / name).is_file():
        return ""
    return f"images/{name}"


def card(spot: dict) -> dict:
    return {
        "id": spot["id"], "name": spot["name"], "description": spot["card_description"],
        "highlights": spot.get("highlights", [])[:3], "map_url": spot.get("map_url", ""),
        "detail_choice_id": f"detail_{spot['id']}",
        "comment": SPOT_COMMENTS.get(spot["id"], ""),
        # モーダル用（spots.json の既存項目をそのまま渡す。「なし」の出し分けは画面側）
        "area": spot.get("area", ""), "hours": spot.get("hours", ""), "closed_days": spot.get("closed_days", ""),
        "photo": photo_url(spot),
        # モーダルではカード説明の代わりに出す長めの説明（同期元の detail_description。空ならカード説明を出す）
        "detail": spot.get("detail_description", ""),
    }


def plan(sess: dict, choice_id: str | None, message: str | None, spots: list[dict]) -> dict:
    """1往復の中身を決める。

    返す辞書: kind（"fixed"=決まった返事 / "ai"=AIに聞く）、topic、choices、
    fixed なら reply と spots、ai なら question（AIに渡す文）。
    """
    by_id = {s["id"]: s for s in spots}

    if choice_id == "spot":
        return {"kind": "fixed", "topic": "spot", "reply": "ほう、今どんな気分だ？", "spots": [],
                "choices": [{"id": k, "label": v[0]} for k, v in MOODS.items()]}

    if choice_id in MOODS:
        tags = MOODS[choice_id][1]
        hits = [s for s in spots if tags & set(s["tags"])]
        picked = random.sample(hits, min(3, len(hits)))
        sess["seen"] = True
        return {"kind": "fixed", "topic": "spot", "reply": "ふむ、このあたりはどうだ。気になる所があれば詳しく話すぞ。",
                "spots": [card(s) for s in picked],
                "choices": []}

    if choice_id == "history":
        return {"kind": "fixed", "topic": "history", "reply": "この地の話なら任せよ。何から聞きたい？", "spots": [],
                "choices": [{"id": k, "label": v[0]} for k, v in HISTORY_TOPICS.items()]}

    if choice_id in HISTORY_TOPICS:
        sess["asked"].add(choice_id)
        sess["seen"] = True
        rest = [{"id": k, "label": v[0]} for k, v in HISTORY_TOPICS.items() if k not in sess["asked"]][:2]
        return {"kind": "ai", "topic": "history", "question": HISTORY_TOPICS[choice_id][1],
                "shown": HISTORY_TOPICS[choice_id][0], "choices": rest}

    if choice_id and choice_id.startswith("detail_"):
        spot = by_id[choice_id[len("detail_"):]]
        sess["seen"] = True
        return {"kind": "ai", "topic": "spot", "question": f"「{spot['name']}」について、見どころを詳しく教えてほしい。",
                "shown": f"{spot['name']}についてもっと聞く",
                "choices": []}

    # 自由入力
    from topic import classify
    topic = classify(message or "", prev_topic=sess.get("topic", "other"))
    return {"kind": "ai", "topic": topic, "question": message, "shown": message,
            "choices": []}
