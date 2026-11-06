"""ユーザーの発言が「歴史」「スポット」「その他」のどれかを判定する。

AIを呼ばず、ボタンのIDとキーワードだけで決める（費用0円、速い、結果が毎回同じ）。
歴史と判定したときだけ、史実の資料を指示文に入れる。

判定を外したときの影響:
- 歴史なのに「その他」と判定 → 資料なしで答える。persona.md のルールでぼかすので、誤情報にはならない（答えが薄くなるだけ）。
- 歴史でないのに「歴史」と判定 → 資料が入って費用が少し増えるだけ。
なので迷ったら歴史寄りに倒す。
"""

HISTORY_WORDS = [
    "富樫", "家通", "歴史", "史実", "昔", "時代", "何年", "いつ", "年号",
    "城", "館", "跡", "守護", "武将", "武士", "侍", "合戦", "戦", "一揆", "一向",
    "加賀", "鎌倉", "室町", "戦国", "南北朝", "幕府", "将軍", "殿", "家来", "一族", "子孫", "先祖",
    "由来", "ゆかり", "伝説", "安宅", "勧進帳", "弁慶", "義経",
]
SPOT_WORDS = [
    "おすすめ", "オススメ", "スポット", "行きたい", "行ける", "どこ", "場所", "近く",
    "カフェ", "ご飯", "食べ", "ランチ", "お土産", "遊", "休", "疲れ", "暇", "写真", "映え",
]
# 前の話を受けているだけの短い発言（「もっと詳しく」など）。直前の話題を引き継ぐ。
FOLLOWUP_WORDS = [
    "それ", "その", "そこ", "あれ", "もっと", "詳しく", "なんで", "なぜ", "どうして", "ほかに", "他に", "続き",
    "へえ", "へー", "なるほど", "すごい", "うん", "そう", "ふーん",
]


def classify(text: str = "", choice_id: str = "", prev_topic: str = "other") -> str:
    if choice_id:
        if choice_id.startswith("history"):
            return "history"
        if choice_id.startswith("spot") or choice_id.startswith("mood"):
            return "spot"
        return "other"

    is_history = any(w in text for w in HISTORY_WORDS)
    is_spot = any(w in text for w in SPOT_WORDS)
    if is_history:
        return "history"  # 「富樫氏ゆかりの場所はどこ？」のように両方なら歴史を優先（資料が必要なので）
    if is_spot:
        return "spot"
    if any(w in text for w in FOLLOWUP_WORDS):
        return prev_topic  # 「もっと詳しく」「なるほど」などは直前の話題のまま
    return "other"
