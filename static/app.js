// 画面の動き。会話API仕様 v0.1 の3つのAPI（/api/session, /api/chat, /api/survey）だけを使う。

// アンケートの4問。q はアンケート担当が決めたキー（2026-10-26決定）。文言は案
const SURVEY_QUESTIONS = [
  { q: "want_to_know_teach_me", text: "知りたいことをちゃんと教えてくれた" },
  { q: "ez_to_use", text: "使いやすかった" },
  { q: "want_to_know_more", text: "富樫氏のことをもっと知りたくなった" },
  { q: "want_to_visit", text: "おすすめのスポットに行ってみたくなった" },
];

const NETWORK_ERROR_TEXT = "すまぬ、うまく聞き取れなんだ。もう一度送ってくれぬか。";

let sessionId = null;
let busy = false; // 返事を待っているあいだは true（二重送信を防ぐ）

const $ = (id) => document.getElementById(id);

// ---------- APIの呼び出し ----------

async function callApi(path, body) {
  if (CONFIG.USE_MOCK) return mockApi(path, body);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000); // 15秒で諦める
  try {
    const res = await fetch(CONFIG.API_BASE_URL + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await res.json();
    if (!res.ok) throw Object.assign(new Error(data.error?.code || "http_error"), { code: data.error?.code });
    return data;
  } finally {
    clearTimeout(timer);
  }
}

// ブースPCは ?device=pc 付きのURLで開く。何もなければスマホ
function getDevice() {
  return new URLSearchParams(location.search).get("device") === "pc" ? "pc" : "phone";
}

// ---------- 画面の切り替え ----------

function showStart() {
  sessionId = null;
  setBusy(false); // 前の体験の終わりで止めたボタンを戻す
  $("messages").innerHTML = "";
  $("start-screen").hidden = false;
  $("chat-screen").hidden = true;
  $("chat-footer").hidden = true;
  $("end-button").hidden = true;
  $("turns-left").hidden = true;
  $("survey-modal").hidden = true;
  $("spot-modal").hidden = true;
}

function showChat() {
  $("start-screen").hidden = true;
  $("chat-screen").hidden = false;
  $("chat-footer").hidden = false;
  $("end-button").hidden = false;
}

function updateTurnsLeft(n) {
  if (typeof n !== "number") return;
  $("turns-left").hidden = false;
  $("turns-left").textContent = `残り${n}回`;
  $("turns-left").classList.toggle("few", n <= 3);
}

function setBusy(value) {
  busy = value;
  // 過去ターンの選択肢（.choices.stale）は無効のまま戻さない
  document.querySelectorAll("#chat-footer button, #chat-footer input, #end-button, .choices:not(.stale) .choice, .card-actions button:not(.detail-button), #spot-modal-ask")
    .forEach((el) => { el.disabled = value; });
}

// ---------- 会話の表示 ----------

function addUserMessage(text) {
  const row = document.createElement("div");
  row.className = "message user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  $("messages").appendChild(row);
}

// /api/session と /api/chat の返事は同じ形なので、表示はこの関数1つで行う
function renderReply(data) {
  addBotBubble().textContent = data.reply;
  renderExtras(data);
}

// 家通の吹き出しを作って返す（SSEのときは、ここに文字を少しずつ足す）
function addBotBubble() {
  // 新しい返事が出たら、それより前の選択肢は押せなくして薄くする（最新ターンの選択肢だけ有効）
  document.querySelectorAll(".choices:not(.stale)").forEach((box) => {
    box.classList.add("stale");
    box.querySelectorAll(".choice").forEach((b) => { b.disabled = true; });
  });
  const row = document.createElement("div");
  row.className = "message";
  row.innerHTML = '<div class="avatar">家</div>';
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  row.appendChild(bubble);
  $("messages").appendChild(row);
  return bubble;
}

// 返事についてくるカード・ボタン・残り回数を表示する
function renderExtras(data) {
  const box = $("messages");
  updateTurnsLeft(data.turns_left);

  if (data.spots && data.spots.length > 0) {
    const cards = document.createElement("div");
    cards.className = "cards";
    data.spots.forEach((spot) => cards.appendChild(renderCard(spot)));
    box.appendChild(cards);
  }

  if (data.choices && data.choices.length > 0 && !data.ended) {
    const choices = document.createElement("div");
    choices.className = "choices";
    data.choices.forEach((choice) => {
      const button = document.createElement("button");
      button.className = "choice";
      button.textContent = choice.label;
      button.addEventListener("click", () => sendChat({ choice_id: choice.id }, choice.label));
      choices.appendChild(button);
    });
    box.appendChild(choices);
  }

  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

// カードの1行要約: 説明の最初の1文（長ければ先頭40字＋…）
function summarize(text) {
  const first = (text || "").split("。")[0];
  return first.length > 40 ? first.slice(0, 40) + "…" : first + (text && text.includes("。") ? "。" : "");
}

let cardCount = 0;

// スポットのカード。最初は閉じていて、名前と1行要約だけ見える。名前の行を押すと開閉（複数同時に開ける）
function renderCard(spot) {
  const card = document.createElement("article");
  card.className = "card";
  const bodyId = `card-body-${++cardCount}`;

  const heading = document.createElement("h3");
  heading.className = "card-heading";
  const head = document.createElement("button");
  head.type = "button";
  head.className = "card-head";
  head.setAttribute("aria-expanded", "false");
  head.setAttribute("aria-controls", bodyId);
  const titles = document.createElement("span");
  titles.className = "card-titles";
  const name = document.createElement("span");
  name.className = "card-name";
  name.textContent = spot.name;
  const summary = document.createElement("span");
  summary.className = "card-summary";
  summary.textContent = summarize(spot.description);
  titles.append(name, summary);
  const mark = document.createElement("span");
  mark.className = "card-toggle";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = "▽";
  head.append(titles, mark);
  heading.appendChild(head);
  card.appendChild(heading);

  const body = document.createElement("div");
  body.className = "card-body";
  body.id = bodyId;
  body.hidden = true;
  card.appendChild(body);

  head.addEventListener("click", () => {
    const open = head.getAttribute("aria-expanded") !== "true";
    head.setAttribute("aria-expanded", String(open));
    body.hidden = !open;
    card.classList.toggle("open", open);
    mark.textContent = open ? "△" : "▽";
  });

  const desc = document.createElement("p");
  desc.className = "card-desc";
  desc.textContent = spot.description;
  body.appendChild(desc);

  if (spot.highlights && spot.highlights.length > 0) {
    const list = document.createElement("ul");
    list.className = "card-highlights";
    spot.highlights.forEach((h) => {
      const li = document.createElement("li");
      li.textContent = h;
      list.appendChild(li);
    });
    body.appendChild(list);
  }

  const actions = document.createElement("div");
  actions.className = "card-actions";
  if (spot.map_url) {
    const map = document.createElement("a");
    map.href = spot.map_url;
    map.target = "_blank"; // 地図は別タブで開き、会話は残す
    map.rel = "noopener";
    map.textContent = "地図で見る";
    actions.appendChild(map);
  }
  // 「詳しく見る」はカードごと。モーダルを開くだけで、会話は送らない（全部開かれてもAIを呼ばないため。2026-11-06決定）
  if (spot.detail_choice_id) {
    const detail = document.createElement("button");
    detail.className = "detail-button";
    detail.textContent = "詳しく見る";
    detail.addEventListener("click", () => openSpotModal(spot));
    actions.appendChild(detail);
  }
  body.appendChild(actions);
  return card;
}

// ---------- 送信 ----------

async function startSession() {
  try {
    const data = await callApi("/api/session", { device: getDevice() });
    sessionId = data.session_id;
    showChat();
    renderReply(data);
  } catch (e) {
    alert("サーバにつながりませんでした。少し待ってからもう一度押してください。");
  }
}

// body は { message: "..." } か { choice_id: "..." } のどちらか
async function sendChat(body, shownText) {
  if (busy || !sessionId) return;
  addUserMessage(shownText);
  setBusy(true);
  try {
    const data = CONFIG.USE_MOCK
      ? await callApi("/api/chat", { session_id: sessionId, ...body }).then((d) => { renderReply(d); return d; })
      : await streamChat({ session_id: sessionId, ...body });
    if (data.ended) {
      if (data.turns_left === 0) addNotice("話せる回数が終わりました。");
      // 別れのあいさつを少し見せてからアンケートへ
      setTimeout(openSurvey, 2000);
      return; // 入力は閉じたまま
    }
  } catch (e) {
    if (e.code === "session_not_found") {
      alert("時間がたったので、はじめからになります。");
      showStart();
      return;
    }
    renderReply({ reply: NETWORK_ERROR_TEXT, choices: [], spots: [] });
  }
  setBusy(false);
}

// SSEで返事を少しずつ表示する。delta の文字を足していき、最後の done（/api/chat の通常のJSONと同じ形）で仕上げる
async function streamChat(payload) {
  const controller = new AbortController();
  let timer = setTimeout(() => controller.abort(), 15000); // 15秒なにも届かなければ諦める
  try {
    const res = await fetch(CONFIG.API_BASE_URL + "/api/chat?stream=1", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw Object.assign(new Error(err.error?.code || "http_error"), { code: err.error?.code });
    }
    const bubble = addBotBubble();
    bubble.classList.add("typing");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      clearTimeout(timer);
      timer = setTimeout(() => controller.abort(), 15000);
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const event = (block.match(/^event: (.*)$/m) || [])[1];
        const data = JSON.parse((block.match(/^data: (.*)$/m) || [])[1] || "{}");
        if (event === "delta") {
          bubble.textContent += data.text;
          window.scrollTo({ top: document.body.scrollHeight });
        } else if (event === "done") {
          bubble.classList.remove("typing");
          bubble.textContent = data.reply; // 途中で失敗したときは定型文に置き換わる
          renderExtras(data);
          return data;
        }
      }
    }
    throw new Error("stream_ended_without_done");
  } finally {
    clearTimeout(timer);
  }
}

function addNotice(text) {
  const p = document.createElement("p");
  p.className = "notice";
  p.textContent = text;
  $("messages").appendChild(p);
}

// ---------- アンケート ----------

function buildSurvey() {
  const area = $("survey-questions");
  area.innerHTML = "";
  SURVEY_QUESTIONS.forEach((item, i) => {
    const fieldset = document.createElement("fieldset");
    fieldset.className = "question";
    const legend = document.createElement("legend");
    legend.textContent = `${i + 1}. ${item.text}`;
    fieldset.appendChild(legend);

    const scale = document.createElement("div");
    scale.className = "scale";
    for (let v = 1; v <= 5; v++) {
      const label = document.createElement("label");
      label.innerHTML = `<input type="radio" name="${item.q}" value="${v}"><span>${v}</span>`;
      scale.appendChild(label);
    }
    fieldset.appendChild(scale);

    const ends = document.createElement("div");
    ends.className = "scale-ends";
    ends.innerHTML = "<span>まったくそう思わない</span><span>とてもそう思う</span>";
    fieldset.appendChild(ends);
    area.appendChild(fieldset);
  });
}

// ---------- スポットの詳しい説明（モーダル） ----------
// spots.json の既存項目（flow.card() が渡す）を出すだけ。AIは呼ばない（残り回数も減らない）。写真は Step 2 で入れる
let modalSpot = null; // いまモーダルで開いているスポット（「もっと聞く」で使う）

function openSpotModal(spot) {
  modalSpot = spot;
  $("spot-modal-title").textContent = spot.name;
  $("spot-modal-comment-text").textContent = spot.comment || "";
  $("spot-modal-comment").hidden = !spot.comment; // ひと言がまだないスポットでは出さない

  $("spot-modal-desc").textContent = spot.description || "";
  $("spot-modal-desc").hidden = !spot.description;

  const list = $("spot-modal-highlights");
  list.replaceChildren(...(spot.highlights || []).map((h) => {
    const li = document.createElement("li");
    li.textContent = h;
    return li;
  }));
  list.hidden = list.children.length === 0;

  // 「なし」や空の項目は行ごと出さない（公園・史跡は営業時間・休みとも「なし」）
  const info = $("spot-modal-info");
  info.replaceChildren();
  [["エリア", spot.area], ["営業時間", spot.hours], ["休み", spot.closed_days]].forEach(([label, value]) => {
    if (!value || value === "なし") return;
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    info.append(dt, dd);
  });
  info.hidden = info.children.length === 0;
  $("spot-modal-body").scrollTop = 0;
  $("spot-modal").hidden = false;
}

function closeSpotModal() {
  $("spot-modal").hidden = true;
}

// 吹き出しの「もっと聞く」: モーダルを閉じて、家通にそのスポットの話をしてもらう（残り回数を1回使う。2026-11-06 C-1案）
$("spot-modal-ask").addEventListener("click", () => {
  if (busy || !modalSpot || !modalSpot.detail_choice_id) return;
  const spot = modalSpot;
  closeSpotModal();
  sendChat({ choice_id: spot.detail_choice_id }, `${spot.name}についてもっと聞く`);
});
$("spot-modal-close").addEventListener("click", closeSpotModal);
$("spot-modal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeSpotModal(); }); // 外側を押しても閉じる
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("spot-modal").hidden) closeSpotModal(); });

function openSurvey() {
  buildSurvey();
  $("survey-form-area").hidden = false;
  $("survey-thanks").hidden = true;
  $("survey-modal").hidden = false;
}

// 会話API仕様 v0.1（2026-10-29更新）に合わせる。
// 「答えない」は skipped: true と空の answers。「送信する」は skipped: false と、答えた問だけ。
// 答えなかった問はサーバが null として保存する（ログ担当の案）
async function submitSurvey(skip) {
  const answers = [];
  if (!skip) {
    SURVEY_QUESTIONS.forEach((item) => {
      const checked = document.querySelector(`input[name="${item.q}"]:checked`);
      if (checked) answers.push({ q: item.q, value: Number(checked.value) });
    });
  }
  $("survey-submit").disabled = true;
  $("survey-skip").disabled = true;
  try {
    await callApi("/api/survey", { session_id: sessionId, skipped: skip, answers });
  } catch (e) {
    // 送れなくても体験は止めない（ログに残らないだけ）
    console.warn("survey failed", e);
  }
  $("survey-submit").disabled = false;
  $("survey-skip").disabled = false;
  $("survey-form-area").hidden = true;
  $("survey-thanks").hidden = false;
}

// ---------- ボタンの登録 ----------

$("start-button").addEventListener("click", startSession);

document.querySelectorAll(".menu-button").forEach((button) => {
  button.addEventListener("click", () =>
    sendChat({ choice_id: button.dataset.choice }, button.querySelector(".menu-ja").textContent));
});

$("input-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("input-text").value.trim();
  if (!text) return;
  $("input-text").value = "";
  sendChat({ message: text }, text);
});

$("end-button").addEventListener("click", () => sendChat({ choice_id: "end" }, "おわる"));

$("survey-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitSurvey(false);
});
$("survey-skip").addEventListener("click", () => submitSurvey(true));
$("restart-button").addEventListener("click", showStart);

// ---------- モック（サーバなしで動かすための固定の返事） ----------

let mockTurnsLeft = 10;

function mockApi(path, body) {
  const wait = (data) => new Promise((resolve) => setTimeout(() => resolve(data), 500));
  const base = { choices: [], spots: [], fallback: false, ended: false };

  if (path === "/api/session") {
    mockTurnsLeft = 10;
    return wait({
      ...base,
      session_id: "mock_session",
      reply: "我は富樫家通。この地のことや、今の世の面白きものを少し語れる者よ。気楽に声をかけてくれ。",
      turns_left: mockTurnsLeft,
    });
  }

  if (path === "/api/survey") {
    console.log("survey", JSON.stringify(body));
    return wait({ ok: true });
  }

  // /api/chat
  mockTurnsLeft -= 1;
  const id = body.choice_id;
  let data;
  if (id === "end" || mockTurnsLeft <= 0) {
    data = { ...base, reply: "よう参ってくれた。またいつでも声をかけよ。", ended: true };
  } else if (id === "spot") {
    data = {
      ...base,
      reply: "今どんな気分だ？",
      choices: [
        { id: "mood_history", label: "歴史を知れる場所" },
        { id: "mood_rest", label: "少し一休みできる場所" },
        { id: "mood_food", label: "おなかがすいた" },
      ],
    };
  } else if (id && id.startsWith("mood_")) {
    data = {
      ...base,
      reply: "ふむ、このあたりはどうだ。",
      spots: [
        {
          id: "spot_001", name: "御経塚遺跡公園",
          description: "縄文時代の大集落跡を保存する国指定史跡の公園。",
          highlights: ["竪穴住居を復元", "自然豊かな遊歩道"],
          map_url: "https://www.google.com/maps/search/?api=1&query=御経塚遺跡",
          detail_choice_id: "detail_spot_001",
          comment: "（モック）縄文の世の大きな村の跡じゃ。竪穴住居も建て直されておる。",
        },
        {
          id: "spot_002", name: "末松廃寺跡",
          description: "白鳳時代に建てられた北陸最古級の寺院跡。",
          highlights: ["国指定史跡", "歴史散策に最適"],
          map_url: "https://www.google.com/maps/search/?api=1&query=末松廃寺跡",
          detail_choice_id: "detail_spot_002",
          comment: "（モック）白鳳の世に建てられた、北陸でも最古級の寺の跡じゃ。",
        },
      ],
    };
  } else if (id && id.startsWith("detail_")) {
    data = { ...base, reply: "（モック）このスポットについて、家通が詳しく話す。" };
  } else if (id === "history") {
    data = {
      ...base,
      reply: "この地の話なら、いくらでも語れるぞ。",
      choices: [{ id: "h_nonoichi", label: "野々市市について聞く" }, { id: "h_togashi", label: "富樫氏について聞く" }],
    };
  } else if (id && id.startsWith("h_")) {
    // サーバと同じく、まだ聞いていない歴史の話題だけを次の選択肢に出す
    const rest = [{ id: "h_nonoichi", label: "野々市市について聞く" }, { id: "h_togashi", label: "富樫氏について聞く" }]
      .filter((c) => c.id !== id);
    data = { ...base, reply: "（モック）史実担当の資料に沿った答えがここに入る。", choices: rest };
  } else {
    data = { ...base, reply: `（モック）「${body.message}」への返事がここに入る。` };
  }
  return wait({ ...data, turns_left: Math.max(mockTurnsLeft, 0) });
}
