/*
  Компонент «Результат проверки» — тот же формат, что в чате и на картинке.
  renderCard(check) возвращает готовый DOM-элемент с кнопками.
*/
import { api, el, tr, haptic, toast, inTelegram, downloadBlob, openLink } from "./core.js";

/** Исходный текст с подсветкой цитат (<mark>). Строим из текстовых узлов — безопасно. */
function highlighted(text, quotes) {
  const lower = text.toLowerCase();
  const spans = [];
  for (const q of quotes) {
    const needle = q.trim().toLowerCase();
    if (needle.length < 2) continue;
    const start = lower.indexOf(needle);
    if (start >= 0) spans.push([start, start + needle.length]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  const box = el("div", { class: "quote-box" });
  let pos = 0;
  for (const [start, end] of spans) {
    if (start < pos) continue; // пересекается с предыдущей
    box.append(text.slice(pos, start), el("mark", { text: text.slice(start, end) }));
    pos = end;
  }
  box.append(text.slice(pos));
  return box;
}

function list(cls, items) {
  return el("ul", { class: cls }, items.map((x) => el("li", { text: x })));
}

function section(title, ...content) {
  return [el("h3", { text: title }), ...content];
}

export function renderCard(check, { onChange } = {}) {
  const card = check.card;
  const support = card.kind === "support"; // поддержка вместо вердикта (кризис): без статуса и уверенности
  const truth = check.truth; // только у «Правды»: шкала, счётчик, проверка модератором
  const root = el("article", { class: `card verdict verdict--${support ? "support" : card.status}` });

  root.append(
    el("div", { class: "result-kicker", text: `🔎 ${tr("ui2.check.result")}` }),
    support ? el("span", { class: "status-pill" }, tr("card.status.support"))
      : truth ? el("div", { class: "row" }, el("span", { class: "status-pill" }, truth.label),
        truth.times_checked > 1 ? el("span", { class: "hint", text: tr("ui.truth.times", { n: truth.times_checked }) }) : null)
        : el("span", { class: "status-pill" }, tr(`card.status.${card.status}`)),
    el("h2", { class: "verdict__title", text: card.title }),
  );
  if (truth) root.append(truthBox(check, truth, onChange));
  if (card.answer) root.append(el("div", { class: "answer-box" }, el("b", { text: tr("card.answer") + " " }), card.answer));

  const quotes = card.reasons.map((r) => r.quote).filter(Boolean);
  if (check.input_text && check.input_text !== "(файл)") {
    root.append(el("details", { open: quotes.length ? true : undefined },
      el("summary", { class: "hint", text: tr("ui.card.original") }), highlighted(check.input_text, quotes)));
  }

  if (card.reasons.length) {
    root.append(...section(tr("card.reasons"), el("ol", { class: "reasons" }, card.reasons.map((r) =>
      el("li", {}, r.text, r.quote ? el("span", {}, " — ", el("q", { text: r.quote })) : null)))));
  }

  if (card.dispute) {
    const d = card.dispute;
    root.append(el("h3", { text: tr("card.dispute.a") }), el("p", { text: d.position_a }),
      el("h3", { text: tr("card.dispute.b") }), el("p", { text: d.position_b }));
    for (const [key, items] of [["card.dispute.facts", d.confirmed_facts], ["card.dispute.claims", d.unconfirmed_claims],
      ["card.dispute.prove", d.to_prove], ["card.dispute.options", d.options]]) {
      if (items.length) root.append(...section(tr(key), list("do-list", items)));
    }
    if (d.neutral_message) root.append(...section(tr("card.dispute.message"), copyBlock(d.neutral_message)));
  }

  if (card.rewrite) {
    root.append(...section(tr(card.kind === "announcement" ? "card.announce.better" : "card.rewrite.calm"), copyBlock(card.rewrite.calm_text)));
    if (card.rewrite.how_it_sounds) root.append(el("p", { class: "hint" }, tr("card.rewrite.sounds"), " ", card.rewrite.how_it_sounds));
  }

  if (card.document) {
    const doc = card.document;
    const rows = [["card.doc.want", doc.what_they_want], ["card.doc.amount", doc.amount], ["card.doc.deadline", doc.deadline],
      ["card.doc.where", doc.where_to_go], ["card.doc.ignore", doc.if_ignore]].filter(([, v]) => v);
    root.append(el("div", { class: "stack" }, rows.map(([k, v]) => el("div", {}, el("b", { text: tr(k) + " " }), v))));
  }

  if (card.sources.length) {
    root.append(...section(tr("card.sources"), el("ul", { class: "sources" }, card.sources.map((s) =>
      el("li", {},
        s.url ? el("a", { href: s.url, text: s.title, onclick: (e) => { e.preventDefault(); openLink(s.url); } }) : s.title,
        s.is_demo ? el("span", { class: "demo-tag", text: tr("ui.card.demo_source") }) : null,
        s.date ? el("span", { class: "hint", text: ` · ${tr("card.actual_on")} ${s.date}` }) : null)))));
  }

  if (!support) {
    root.append(el("h3", { text: tr("card.confidence") }),
      el("div", { class: "conf" }, el("div", { class: "conf__bar" }, el("span", { style: `width:${card.confidence}%` })), el("b", { text: `${card.confidence}%` })));
  }

  if (card.do.length) root.append(...section(tr("card.do"), list("do-list", card.do)));
  if (card.dont.length) root.append(...section(tr("card.dont"), list("dont-list", card.dont)));
  if (card.claim_letter) root.append(...section(tr("card.claim_letter"), copyBlock(card.claim_letter)));

  if (check.user_sources && check.user_sources.length) {
    root.append(...section(tr("ui.card.user_sources"), el("ul", { class: "sources" }, check.user_sources.map((s) =>
      el("li", {}, el("a", { href: s.url, text: s.url, onclick: (e) => { e.preventDefault(); openLink(s.url); } }), s.note ? ` — ${s.note}` : "")))));
  }

  if (card.notes.length) root.append(el("div", { class: "notes" }, card.notes.map((n) => el("div", { text: n }))));

  if (check.id) root.append(actions(check, root, onChange));
  root.append(el("p", { class: "disclaimer", text: tr("card.disclaimer") }));
  return root;
}

/** Слух: что дальше. Не подтверждено → как проверить самому и «Отправить модератору»;
    на проверке → результат придёт в бот; модератор уже решил → его результат и ссылка на ленту «Проверенные слухи». */
function truthBox(check, truth, onChange) {
  const r = truth.review;
  if (r && r.status === "approved" && r.verdict) {
    const repeated = check.card.reasons.some((x) => x.text === r.comment); // пояснение уже стоит в «Почему»
    return el("div", { class: "callout" }, el("b", { text: tr("ui.truth.moderated", { label: truth.label }) }),
      r.comment && !repeated ? el("p", { text: r.comment }) : null,
      el("a", { href: "#facts", text: tr("ui.truth.to_feed") }));
  }
  if (r && r.status === "pending") {
    return el("div", { class: "callout" }, el("b", { text: tr("ui.truth.pending") }), el("p", { class: "hint", text: tr("ui.truth.pending_sub") }));
  }
  if (truth.code !== "unconfirmed" && !truth.can_escalate) return el("div", {});
  const box = el("div", { class: "callout stack" }, el("p", { text: tr("ui.truth.howto") }));
  if (truth.can_escalate) {
    const btn = el("button", { class: "btn btn--primary btn--small", type: "button", text: tr("ui.truth.escalate"), onclick: async () => {
      btn.disabled = true;
      try {
        const res = await api(`/api/checks/${check.id}/escalate`, { method: "POST" });
        haptic("success");
        toast(res.joined ? tr("ui.truth.joined") : tr("ui.truth.escalated"));
        if (onChange) onChange();
      } catch (e) { toast(e.message); btn.disabled = false; }
    } });
    box.append(btn, el("p", { class: "hint", text: tr("ui.truth.escalate_hint") }));
  }
  return box;
}

function copyBlock(text) {
  const wrap = el("div", { class: "stack" }, el("div", { class: "pre", text }));
  wrap.append(el("button", { class: "btn btn--secondary btn--small", text: tr("ui.copy"),
    onclick: async () => { try { await navigator.clipboard.writeText(text); toast(tr("ui.copied")); } catch { window.prompt(tr("ui.copy"), text); } } }));
  return wrap;
}

/** Кнопки под карточкой: голосование, источник, картинка, «проще». */
function actions(check, root, onChange) {
  const box = el("div", { class: "stack" });
  const votes = check.votes || { agree: 0, disagree: 0 };
  const agreeBtn = el("button", { class: "btn btn--secondary btn--small" });
  const disagreeBtn = el("button", { class: "btn btn--secondary btn--small" });
  const paintVotes = () => {
    agreeBtn.textContent = votes.agree ? `${tr("ui.card.agree")} · ${votes.agree}` : tr("ui.card.agree");
    disagreeBtn.textContent = votes.disagree ? `${tr("ui.card.disagree")} · ${votes.disagree}` : tr("ui.card.disagree");
  };
  paintVotes();
  const vote = async (value) => {
    haptic("light");
    try {
      Object.assign(votes, await api(`/api/checks/${check.id}/vote`, { method: "POST", body: { value } }));
      paintVotes();
      toast(tr("ui.card.voted"));
    } catch (e) { toast(e.message); }
  };
  agreeBtn.addEventListener("click", () => vote(1));
  disagreeBtn.addEventListener("click", () => vote(-1));

  // Форма «Добавить источник»
  const sourceInput = el("input", { class: "input", type: "url", placeholder: tr("ui.card.source_placeholder") });
  const sourceForm = el("div", { class: "row", hidden: true }, sourceInput,
    el("button", { class: "btn btn--primary btn--small", text: tr("ui.card.source_add"), onclick: async () => {
      try {
        await api(`/api/checks/${check.id}/sources`, { method: "POST", body: { url: sourceInput.value } });
        toast(tr("ui.card.source_added"));
        sourceForm.hidden = true;
        if (onChange) onChange();
      } catch (e) { toast(e.message); }
    } }));

  const extra = el("div", { class: "stack" }); // сюда выводим «проще» и картинку

  const imageBtn = el("button", { class: "btn btn--secondary btn--small", text: tr("ui.card.image"), onclick: async () => {
    imageBtn.disabled = true;
    try {
      if (inTelegram) {
        await api(`/api/checks/${check.id}/send-image`, { method: "POST" });
        toast(tr("ui.card.image_sent"));
      } else {
        const blob = await (await api(`/api/checks/${check.id}/image.png`, { raw: true })).blob();
        extra.replaceChildren(el("img", { class: "card-image", src: URL.createObjectURL(blob), alt: tr("ui.card.image_alt") }));
        downloadBlob(blob, `rezultat_proverki_${check.id}.png`);
      }
    } catch (e) { toast(e.message); }
    imageBtn.disabled = false;
  } });

  const simpleBtn = el("button", { class: "btn btn--secondary btn--small", text: tr("ui.card.simpler"), onclick: async () => {
    simpleBtn.disabled = true;
    try {
      const { text } = await api(`/api/checks/${check.id}/simplify`, { method: "POST" });
      extra.replaceChildren(el("div", { class: "pre" }, el("b", { text: tr("ui.card.simple") + ": " }), text));
    } catch (e) { toast(e.message); }
    simpleBtn.disabled = false;
  } });

  box.append(
    el("div", { class: "actions" }, agreeBtn, disagreeBtn,
      el("button", { class: "btn btn--secondary btn--small", text: tr("ui.card.add_source"), onclick: () => { sourceForm.hidden = !sourceForm.hidden; } }),
      imageBtn, simpleBtn),
    sourceForm, extra);
  return box;
}
