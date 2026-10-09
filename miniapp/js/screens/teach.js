/* «Преподавателю»: опрос после пары (анонимно, сводка тем без имён), правила ИИ на курсе (с подтверждением
   прочтения), вопросы перед лекцией, запись на консультацию (слоты), отчёт по договорённости.
   Кабинет дисциплины, план курса и карта нагрузки — на странице хаба (для подтверждённых преподавателей).
   #teach, #poll=ID, #slots, #slots=ID_ПРЕПОДАВАТЕЛЯ, #agr_report=КОД */
import { api, el, tr, toast, haptic, skeleton, emptyState, errorState, copyText, state, formatDate } from "../core.js";
import { screenTitle, sectionHead, linkCard, numbered, formatDay, screenHead, EMOJI } from "../ui.js";
import { feature, field, input, textarea, select, isVerifiedTeacher, botDeepLink } from "../campus_ui.js";
import { postRow, pollResults } from "./community.js";

const lines = (text) => text.split("\n").map((x) => x.trim()).filter(Boolean);

export async function render(view, _param, ctx) {
  view.replaceChildren(screenHead(EMOJI.teacher, tr("ui2.teach.title"), tr("ui2.teach.line")), ...skeleton(3));
  const [polls, circles, mine] = await Promise.all([
    feature("class_poll") ? api("/api/polls/mine").catch(() => []) : [],
    api("/api/circles").catch(() => []),
    api("/api/posts/mine").catch(() => []),
  ]);
  const blocks = [];
  let n = 0;

  if (feature("class_poll")) {
    const q = input({ maxlength: 512, value: tr("ui.tc.poll_default") });
    const opts = textarea({ rows: 3, placeholder: tr("ui.tc.poll_options_ph") });
    const circle = select([["", tr("ui.tc.poll_link_only")], ...circles.filter((c) => ["owner", "admin"].includes(c.my_role)).map((c) => [c.id, c.title])], "");
    const out = el("div", { class: "stack" });
    blocks.push(numbered(++n, tr("ui.tc.poll"), el("p", { text: tr("ui.tc.poll_sub") }), el("div", { class: "stack" }, field(tr("ui.tc.question"), q),
      field(tr("ui.tc.poll_options"), opts), field(tr("ui.tc.poll_group"), circle),
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.tc.poll_create"), onclick: async () => {
        try {
          const p = await api("/api/polls", { method: "POST", body: { question: q.value, options: lines(opts.value), circle_id: circle.value ? Number(circle.value) : null } });
          haptic("success");
          const link = botDeepLink(`poll_${p.id}`);
          out.replaceChildren(el("p", { class: "hint", text: p.sent ? tr("ui.tc.poll_sent", { n: p.sent }) : tr("ui.tc.poll_share") }),
            link ? el("div", { class: "pre", text: link }) : el("p", { class: "hint", text: tr("ui.agr.no_bot_link") }),
            link ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.agr.copy_link"), onclick: () => copyText(link) }) : null);
        } catch (e) { toast(e.message); }
      } }), out,
      polls.length ? el("div", { class: "list" }, polls.map((p) => linkCard({ title: p.question, sub: tr("ui.tc.poll_row", { n: p.answers, date: formatDate(p.created_at) }), href: `#poll=${p.id}` }))) : null)));
  }

  if (feature("ai_rules")) {
    const title = input({ maxlength: 256, placeholder: tr("ui.tc.ai_title_ph") });
    const allowed = textarea({ rows: 3, placeholder: tr("ui.tc.ai_allowed_ph") });
    const forbidden = textarea({ rows: 3, placeholder: tr("ui.tc.ai_forbidden_ph") });
    const out = el("div", {});
    const rules = mine.filter((p) => p.kind === "ai_rules");
    blocks.push(numbered(++n, tr("ui.tc.ai"), el("p", { text: tr("ui.tc.ai_sub") }), el("div", { class: "stack" }, field(tr("ui.cm.f_title"), title),
      field(tr("ui.tc.ai_allowed"), allowed), field(tr("ui.tc.ai_forbidden"), forbidden),
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.tc.ai_create"), onclick: async () => {
        try {
          const p = await api("/api/posts", { method: "POST", body: { kind: "ai_rules", title: title.value || tr("ui.tc.ai_default_title"), data: { allowed: lines(allowed.value), forbidden: lines(forbidden.value) } } });
          const link = botDeepLink(`ack_${p.id}`);
          out.replaceChildren(el("p", { class: "hint", text: tr("ui.tc.ai_share") }), link ? el("div", { class: "pre", text: link }) : null,
            link ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.agr.copy_link"), onclick: () => copyText(link) }) : null,
            el("a", { class: "more-btn", href: `#post=${p.id}` }, tr("ui.tc.ai_open"), " →"));
        } catch (e) { toast(e.message); }
      } }), out, rules.length ? el("div", { class: "list" }, rules.map(postRow)) : null)));
  }

  if (feature("lecture_questions")) {
    const title = input({ maxlength: 256, placeholder: tr("ui.tc.lecture_ph") });
    const date = input({ type: "date" });
    const boards = mine.filter((p) => p.kind === "lecture");
    blocks.push(numbered(++n, tr("ui.tc.lecture"), el("p", { text: tr("ui.tc.lecture_sub") }), el("div", { class: "stack" }, title, date,
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.tc.lecture_create"), onclick: async () => {
        try { const p = await api("/api/posts", { method: "POST", body: { kind: "lecture", title: title.value, data: { date: date.value } } }); ctx.nav(`#post=${p.id}`); } catch (e) { toast(e.message); }
      } }), boards.length ? el("div", { class: "list" }, boards.map(postRow)) : null)));
  }

  if (feature("consultations")) {
    blocks.push(numbered(++n, tr("ui.tc.slots"), el("p", { text: tr("ui.tc.slots_sub") }), el("a", { class: "more-btn", href: "#slots" }, tr("ui.tc.slots_open"), " →")));
  }
  if (feature("agreement_report")) {
    blocks.push(numbered(++n, tr("ui.tc.report"), el("p", { text: tr("ui.tc.report_sub") }), el("a", { class: "more-btn", href: "#agreements" }, tr("ui.more.agreements"), " →")));
  }
  if (feature("hubs")) {
    blocks.push(numbered(++n, tr("ui.tc.cabinet"), el("p", { text: isVerifiedTeacher() ? tr("ui.tc.cabinet_sub_ok") : tr("ui.tc.cabinet_sub_need") }),
      el("a", { class: "more-btn", href: isVerifiedTeacher() ? "#hubs" : "#hubs=teacher" }, isVerifiedTeacher() ? tr("ui.hub.title") : tr("ui.hub.verify"), " →")));
  }
  view.replaceChildren(screenHead(EMOJI.teacher, tr("ui2.teach.title"), tr("ui2.teach.line")),
    state.me && state.me.role !== "teacher" ? el("p", { class: "card hint", text: tr("ui.tc.role_note") }) : null,
    el("section", { class: "card" }, ...blocks),
    el("p", { class: "disclaimer", text: tr("ui.tc.rules") }));
}

export async function renderPoll(view, id) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let p;
  try { p = await api(`/api/polls/${id}/results`); } catch (e) {
    // Не автор — показываем форму ответа (анонимно).
    try { p = await api(`/api/polls/${id}`); } catch (err) { view.replaceChildren(errorState(err.message, () => renderPoll(view, id))); return; }
    return renderAnswer(view, p);
  }
  view.replaceChildren(el("a", { class: "hint", href: "#teach", text: tr("ui.tc.title") }),
    el("article", { class: "card stack" }, el("h2", { class: "verdict__title", text: p.question }), el("p", { class: "hint", text: tr("ui.cm.answers_total", { n: p.answers }) }),
      pollResults(p),
      p.open ? el("button", { class: "btn btn--secondary btn--block", type: "button", text: tr("ui.tc.poll_close"), onclick: async () => {
        try { await api(`/api/polls/${id}/close`, { method: "POST" }); renderPoll(view, id); } catch (e) { toast(e.message); }
      } }) : el("p", { class: "hint", text: tr("ui.tc.poll_closed") })),
    el("p", { class: "disclaimer", text: tr("ui.tc.poll_privacy") }));
}

function renderAnswer(view, p) {
  const box = el("section", { class: "card stack" }, el("h2", { class: "verdict__title", text: p.question }));
  if (p.answered) box.append(el("p", { class: "hint", text: tr("ui.cm.answered") }));
  else if (p.options.length) {
    box.append(...p.options.map((o, i) => el("button", { class: "option", type: "button", text: o, onclick: async () => {
      try { await api(`/api/polls/${p.id}/answer`, { method: "POST", body: { option: i } }); haptic("success"); toast(tr("ui.cm.answered")); renderPoll(view, p.id); } catch (e) { toast(e.message); }
    } })));
  } else {
    const t = textarea({ maxlength: 1000 });
    box.append(t, el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.cm.answer_send"), onclick: async () => {
      try { await api(`/api/polls/${p.id}/answer`, { method: "POST", body: { text: t.value } }); haptic("success"); toast(tr("ui.cm.answered")); renderPoll(view, p.id); } catch (e) { toast(e.message); }
    } }));
  }
  box.append(el("p", { class: "hint", text: tr("ui.cm.anon_poll", { n: p.min_answers }) }));
  view.replaceChildren(box);
}

// ------------------------------------------------------------------ консультации

export async function renderSlots(view, ownerParam) {
  const me = state.me ? state.me.telegram_id : 0;
  const owner = ownerParam ? Number(ownerParam) : me;
  view.replaceChildren(screenHead(EMOJI.slots, tr("ui.tc.slots")), ...skeleton(2));
  let slots;
  try { slots = await api(`/api/slots?owner_id=${owner}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderSlots(view, ownerParam))); return; }
  const reload = () => renderSlots(view, ownerParam);
  const mine = owner === me;
  const blocks = [];
  if (mine && state.me.role === "teacher") {
    const date = input({ type: "date" });
    const from = input({ type: "time", value: "14:00" });
    const count = input({ type: "number", min: 1, max: 12, value: 4 });
    const minutes = select([[10, "10"], [15, "15"], [20, "20"], [30, "30"]], 15);
    const place = input({ maxlength: 128, placeholder: tr("ui.tc.slot_place") });
    blocks.push(el("section", { class: "card stack" }, el("p", { class: "hint", text: tr("ui.tc.slots_make") }),
      el("div", { class: "row" }, field(tr("ui.pl.date"), date), field(tr("ui.tc.slot_from"), from)),
      el("div", { class: "row" }, field(tr("ui.tc.slot_count"), count), field(tr("ui.tc.slot_minutes"), minutes)), field(tr("ui.tc.slot_place"), place),
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.tc.slots_create"), onclick: async () => {
        if (!date.value) { toast(tr("ui.pl.date")); return; }
        const [hh, mm] = from.value.split(":").map(Number);
        const starts = Array.from({ length: Number(count.value) }, (_, i) => {
          const total = hh * 60 + mm + i * Number(minutes.value);
          return `${date.value}T${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
        });
        try { await api("/api/slots", { method: "POST", body: { starts, minutes: Number(minutes.value), place: place.value } }); haptic("success"); reload(); } catch (e) { toast(e.message); }
      } })));
    const link = botDeepLink(`slot_${me}`);
    if (link) blocks.push(el("section", { class: "card stack" }, el("p", { class: "hint", text: tr("ui.tc.slots_share") }), el("div", { class: "pre", text: link }),
      el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.agr.copy_link"), onclick: () => copyText(link) })));
  }
  view.replaceChildren(screenHead(EMOJI.slots, tr("ui.tc.slots"), mine ? tr("ui.tc.slots_mine") : (slots[0] ? slots[0].owner_name : "")), ...blocks,
    slots.length ? el("div", { class: "list" }, slots.map((s) => el("div", { class: "item" },
      el("div", { class: "item__body" }, el("div", { class: "item__title", text: `${formatDay(s.start.slice(0, 10))}, ${s.start.slice(11)} · ${s.minutes} ${tr("ui.min")}` }),
        el("div", { class: "item__sub", text: [s.place, s.is_owner ? (s.taken ? `${s.taken_name}` : tr("ui.tc.slot_free")) : ""].filter(Boolean).join(" · ") })),
      s.is_owner ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.delete"), onclick: async () => {
        try { await api(`/api/slots/${s.id}`, { method: "DELETE" }); reload(); } catch (e) { toast(e.message); }
      } }) : s.mine ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.hub.unbook"), onclick: async () => {
        try { await api(`/api/slots/${s.id}/book`, { method: "POST", body: { cancel: true } }); reload(); } catch (e) { toast(e.message); }
      } }) : s.taken ? el("span", { class: "hint", text: tr("ui.tc.slot_taken") }) : el("button", { class: "btn btn--primary btn--small", type: "button", text: tr("ui.hub.book"), onclick: async () => {
        try { await api(`/api/slots/${s.id}/book`, { method: "POST", body: {} }); haptic("success"); toast(tr("ui.hub.booked")); reload(); } catch (e) { toast(e.message); }
      } })))) : emptyState("📅", tr("ui.tc.slots_empty")));
}

/** Отчёт по договорённости — на карточке договорённости (для автора). */
export async function reportBlock(code) {
  const box = el("section", { class: "card stack" }, el("h3", { text: tr("ui.tc.report") }));
  try {
    const r = await api(`/api/agreements/${code}/report`);
    box.append(el("p", {}, el("b", { text: tr("ui.tc.r_yes", { n: r.confirmed.length }) + " " }), r.confirmed.join(", ") || "—"),
      r.declined.length ? el("p", {}, el("b", { text: tr("ui.tc.r_no", { n: r.declined.length }) + " " }), r.declined.join(", ")) : null,
      r.known_members ? el("p", {}, el("b", { text: tr("ui.tc.r_wait", { n: r.waiting.length }) + " " }), r.waiting.join(", ") || "—")
        : el("p", { class: "hint", text: tr("ui.tc.r_unknown") }));
  } catch { return null; }
  return box;
}

