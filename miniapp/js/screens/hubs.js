/* Общие страницы (в коде и базе — «хабы»):
   - страница дисциплины (kind=discipline) открывается как предмет: #subject=h<id> (раздел 📚 Предметы);
   - темы для всех (kind=cross: «Первокурснику», «Практика», «Общежитие», Coursera...) — в 💬 IITU Hub: #topics, #topic=ID;
   - #hubs — найти страницу дисциплины и присоединиться, предложить новую, стать ментором (заявки решает модератор);
   - #hub=ID — старая ссылка (бот, закладки): открываем как предмет или как тему.
   Кабинет дисциплины и «Ответ преподавателя» — только после ручного подтверждения статуса преподавателя. */
import { api, el, tr, toast, haptic, skeleton, errorState, emptyState, state, openLink, copyText } from "../core.js";
import { screenHead, sectionHead, linkCard, numbered, formatDay, EMOJI } from "../ui.js";
import { feature, field, input, textarea, select, isTeacher, reportButton, voteButton, botDeepLink } from "../campus_ui.js";
import { postForm, postRow } from "./community.js";

function hubCard(h, href) {
  return linkCard({ emoji: h.emoji, title: h.title, href: href || (h.kind === "discipline" ? `#subject=h${h.id}` : `#topic=${h.id}`),
    sub: [h.course_title, h.members ? tr("ui.hub.members", { n: h.members }) : "", h.my_role ? tr(`ui.hub.role_${h.my_role}`) : ""].filter(Boolean).join(" · ") });
}

/** #hubs — каталог страниц дисциплин (раздел «Предметы») + заявки. */
export async function render(view, param, ctx) {
  if (param === "mentor" || param === "teacher" || param === "request") return renderApply(view, param, ctx);
  const head = screenHead(EMOJI.topics, tr("ui2.hubs.title"), tr("ui2.hubs.line"));
  view.replaceChildren(head, ...skeleton(3));
  let c;
  try { c = await api("/api/hubs"); } catch (e) { view.replaceChildren(head, errorState(e.message, () => render(view, param, ctx))); return; }
  const blocks = [];
  if (c.courses.length) {
    const order = c.my_course ? [...c.courses].sort((a, b) => (b.course === c.my_course) - (a.course === c.my_course)) : c.courses;
    for (const g of order) blocks.push(sectionHead(`${EMOJI.course} ${g.title}`), el("div", { class: "menu-list" }, g.hubs.map((h) => hubCard(h))));
  } else {
    blocks.push(emptyState(EMOJI.topics, tr("ui2.hubs.empty"), null, tr("ui2.soon")));
  }
  if (c.teachers.length) blocks.push(sectionHead(`${EMOJI.teacher} ${tr("ui.hub.teachers_room")}`), el("div", { class: "menu-list" }, c.teachers.map((h) => hubCard(h, `#topic=${h.id}`))));
  blocks.push(sectionHead(tr("ui2.hubs.apply")), el("div", { class: "menu-list" },
    linkCard({ emoji: "➕", title: tr("ui.hub.request"), sub: tr("ui2.hubs.request_sub"), href: "#hubs=request" }),
    feature("mentors") ? linkCard({ emoji: EMOJI.course, title: tr("ui.hub.apply_mentor"), sub: tr("ui2.hubs.mentor_sub"), href: "#hubs=mentor" }) : null,
    isTeacher() && !c.teacher_verified && feature("teacher_cabinet") ? linkCard({ emoji: EMOJI.teacher, title: tr("ui.hub.verify"), sub: tr("ui2.hubs.verify_sub"), href: "#hubs=teacher" }) : null));
  view.replaceChildren(head, ...blocks, el("p", { class: "disclaimer", text: tr("ui.hub.rules") }));
}

/** #topics — темы для всех (IITU Hub). */
export async function renderTopics(view, param, ctx) {
  const head = screenHead(EMOJI.topics, tr("ui2.topics.title"), tr("ui2.topics.line"));
  view.replaceChildren(head, ...skeleton(3));
  let c;
  try { c = await api("/api/hubs"); } catch (e) { view.replaceChildren(head, errorState(e.message, () => renderTopics(view, param, ctx))); return; }
  view.replaceChildren(head, el("div", { class: "menu-list" }, c.cross.map((h) => hubCard(h, `#topic=${h.id}`))),
    el("p", { class: "disclaimer", text: tr("ui.hub.rules") }));
}

async function renderApply(view, kind, ctx) {
  let catalog = { mine: [], cross: [], courses: [] };
  try { catalog = await api("/api/hubs"); } catch { /* список необязателен */ }
  const allHubs = [...catalog.cross, ...catalog.courses.flatMap((g) => g.hubs)];
  const fields = [];
  const values = {};
  const add = (key, label, node) => { values[key] = node; fields.push(field(label, node)); };
  const hubPick = el("div", { class: "chips" }, allHubs.map((h) => el("button", { class: "chip", type: "button", "aria-pressed": "false", "data-id": h.id,
    onclick: (e) => e.currentTarget.setAttribute("aria-pressed", String(e.currentTarget.getAttribute("aria-pressed") !== "true")) }, `${h.emoji} ${h.title}`)));
  if (kind === "mentor") {
    add("course", tr("ui.cm.f_course"), select([["2", "2"], ["3", "3"], ["4", "4"], ["master", tr("ui.hub.master")]], state.me && state.me.course));
    add("subjects", tr("ui.hub.f_subjects"), input({ maxlength: 300 }));
    add("about", tr("ui.hub.f_about"), textarea({ maxlength: 500 }));
  } else if (kind === "teacher") {
    add("department", tr("ui.hub.f_department"), input({ maxlength: 200 }));
    add("subjects", tr("ui.hub.f_subjects"), input({ maxlength: 300 }));
    add("about", tr("ui.hub.f_teacher_about"), textarea({ maxlength: 500 }));
  } else {
    add("title", tr("ui2.hubs.f_title"), input({ maxlength: 128 }));
    add("course", tr("ui.cm.f_course"), select([["", tr("ui.hub.any")], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["master", tr("ui.hub.master")]], ""));
    add("program", tr("ui.hub.f_program"), input({ maxlength: 16, placeholder: "6B06101" }));
    add("description", tr("ui.hub.f_description"), textarea({ maxlength: 500 }));
    add("chat_url", tr("ui.hub.f_chat"), input({ maxlength: 300, placeholder: "https://t.me/..." }));
  }
  const send = el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.hub.apply_send"), onclick: async () => {
    const data = Object.fromEntries(Object.entries(values).map(([k, node]) => [k, node.value]));
    data.hub_ids = [...hubPick.querySelectorAll('[aria-pressed="true"]')].map((b) => Number(b.dataset.id));
    send.disabled = true;
    try {
      await api("/api/hubs/apply", { method: "POST", body: { kind: kind === "request" ? "hub" : kind, data } });
      haptic("success"); toast(tr("ui2.hubs.apply_sent")); ctx.nav("#mine");
    } catch (e) { toast(e.message); }
    send.disabled = false;
  } });
  view.replaceChildren(screenHead(kind === "teacher" ? EMOJI.teacher : kind === "mentor" ? EMOJI.course : "➕", tr(`ui2.hubs.apply_${kind}_title`), tr(`ui.hub.apply_${kind}_sub`)),
    el("section", { class: "card stack" }, ...fields, kind !== "request" ? field(tr("ui2.hubs.f_hubs"), hubPick) : null, send,
      el("p", { class: "hint", text: tr("ui2.hubs.apply_status") })));
}

/** #hub=ID (старые ссылки): дисциплина → предмет, тема → тема. */
export async function open(view, id, ctx) {
  try {
    const h = await api(`/api/hubs/${id}`);
    ctx.replace(h.kind === "discipline" ? `#subject=h${id}` : `#topic=${id}`);
  } catch (e) { view.replaceChildren(errorState(e.message, () => open(view, id, ctx))); }
}

/** #topic=ID — страница темы для всех (IITU Hub). Страница дисциплины открывается как предмет. */
export async function renderOne(view, id, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let h;
  try { h = await api(`/api/hubs/${id}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderOne(view, id, ctx))); return; }
  if (h.kind === "discipline") { ctx.replace(`#subject=h${id}`); return; }
  const reload = () => renderOne(view, id, ctx);
  view.replaceChildren(screenHead(h.emoji, h.title, h.description), ...hubBlocks(h, reload, { startNumber: 0 }),
    el("p", { class: "disclaimer", text: tr("ui.hub.rules") }));
}

/** Блоки общей страницы: участие, объявления, материалы, вопросы, отзывы, консультации, люди, кабинет преподавателя.
    subject=true — блоки встраиваются в страницу предмета (там свои дедлайны и силлабус). */
export function hubBlocks(h, reload, { startNumber = 0, subject = false } = {}) {
  let n = startNumber;
  const id = h.id;
  const blocks = [];
  const joined = Boolean(h.my_role);
  blocks.push(el("section", { class: "card stack" },
    el("div", { class: "row" }, h.course_title ? el("span", { class: "tag", text: h.course_title }) : null,
      h.my_role ? el("span", { class: "tag tag--accent", text: tr(`ui.hub.role_${h.my_role}`) }) : null,
      h.members ? el("span", { class: "hint", text: tr("ui.hub.members", { n: h.members }) }) : null),
    subject && h.description ? el("p", { class: "hint", text: h.description }) : null,
    el("div", { class: "actions" },
      h.my_role === "teacher" || h.my_role === "mentor" || h.my_role === "curator" ? null
        : el("button", { class: joined ? "btn btn--secondary btn--small" : "btn btn--primary btn--small", type: "button", text: joined ? tr("ui.hub.leave") : tr("ui2.hubs.join"), onclick: async () => {
          try { await api(`/api/hubs/${id}/join`, { method: "POST", body: { on: !joined } }); haptic("success"); toast(joined ? tr("ui.saved") : tr("ui2.hubs.joined")); reload(); } catch (e) { toast(e.message); }
        } }),
      h.chat_url ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.hub.chat"), onclick: () => openLink(h.chat_url) }) : null,
      botDeepLink(`hub_${id}`) ? el("button", { class: "btn btn--ghost btn--small", type: "button", text: tr("ui.hub.share"), onclick: () => copyText(botDeepLink(`hub_${id}`)) }) : null),
    el("p", { class: "hint", text: tr("ui2.hubs.join_hint") })));

  if (h.i_am_hub_teacher && feature("teacher_cabinet")) blocks.push(...teacherCabinet(h, reload));
  else if (h.i_am_verified_teacher && feature("teacher_cabinet") && h.kind !== "teachers") {
    blocks.push(el("button", { class: "btn btn--secondary btn--block", type: "button", text: tr("ui.hub.teach"), onclick: async () => {
      try { await api(`/api/hubs/${id}/teach`, { method: "POST" }); haptic("success"); reload(); } catch (e) { toast(e.message); }
    } }));
  }
  if (h.announcements.length) blocks.push(sectionHead(`${EMOJI.announce} ${tr("ui.hub.announcements")}`), el("div", { class: "rows" }, h.announcements.map(postRow)));
  if (!subject && h.deadlines.length) {
    blocks.push(sectionHead(`${EMOJI.deadline} ${tr("ui.hub.deadlines")}`), el("div", { class: "rows" }, h.deadlines.map((d) => el("div", { class: "row-item row-item--static" },
      el("div", { class: "row-item__body" }, el("div", { class: "row-item__title", text: d.title }),
        el("div", { class: "row-item__sub", text: formatDay(d.data.due_date) + (d.data.due_time ? " " + d.data.due_time : "") }))))),
    el("p", { class: "hint", text: tr("ui.hub.deadlines_hint") }));
  }
  if (h.pulse) {
    const p = h.pulse;
    blocks.push(sectionHead(`${EMOJI.polls} ${tr("ui.hub.pulse")}`), el("section", { class: "card stack" }, el("p", { text: p.question }),
      p.answered ? el("p", { class: "hint", text: tr("ui.cm.answered") }) : el("div", { class: "chips" }, p.options.map((o, i) => el("button", { class: "chip", type: "button", text: String(i + 1), title: o, onclick: async () => {
        try { await api(`/api/polls/${p.id}/answer`, { method: "POST", body: { option: i } }); haptic("success"); reload(); } catch (e) { toast(e.message); }
      } }))), el("p", { class: "hint", text: tr("ui.cm.anon_poll", { n: p.min_answers }) })));
  }
  if (feature("resources")) {
    blocks.push(el("div", { id: "s-mat" }, sectionHead(`${EMOJI.materials} ${tr("ui.hub.materials")}`, null, null, subject ? ++n : 0)),
      h.resources.length ? el("div", { class: "rows" }, h.resources.map(postRow)) : el("p", { class: "hint", text: tr("ui.hub.no_materials") }),
      el("details", { class: "card" }, el("summary", { text: `＋ ${tr("ui.hub.add_material")}` }), postForm("resource", { hubId: id, onDone: () => setTimeout(reload, 500) })));
  }
  if (feature("hub_faq") && h.faq.length) {
    blocks.push(sectionHead(`${EMOJI.questions} ${tr("ui.hub.faq")}`), el("div", { class: "list" }, h.faq.map((f) => el("div", { class: "card stack" },
      f.is_teacher ? el("span", { class: "tag tag--accent", text: tr("ui.cm.teacher_answer") }) : null,
      el("b", { text: f.title }), el("p", { class: "pre", text: f.body }),
      el("div", { class: "actions" }, voteButton(f, reload), f.data.link ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.hub.original"), onclick: () => openLink(f.data.link) }) : null,
        reportButton(f.id))))));
  }
  if (feature("ask_senior")) {
    blocks.push(el("div", { id: "s-q" }, sectionHead(`${EMOJI.questions} ${tr("ui2.subj.a_questions")}`, null, null, subject ? ++n : 0)),
      h.unanswered.length ? el("p", { class: "hint", text: tr("ui.hub.unanswered", { n: h.unanswered.length }) }) : null,
      h.questions.length ? el("div", { class: "rows" }, h.questions.map(postRow)) : el("p", { class: "hint", text: tr("ui.hub.no_questions") }),
      postForm("senior_q", { hubId: h.id, onDone: () => setTimeout(reload, 500) }));
  }
  if (h.lectures.length) blocks.push(sectionHead(`${EMOJI.announce} ${tr("ui.hub.lectures")}`), el("div", { class: "rows" }, h.lectures.map(postRow)));
  if (feature("consultations") && h.slots.length) {
    blocks.push(sectionHead(`${EMOJI.slots} ${tr("ui.hub.slots")}`), el("div", { class: "list" }, h.slots.map((s) => el("div", { class: "item" },
      el("div", { class: "item__body" }, el("div", { class: "item__title", text: `${s.start.replace("T", " ")} · ${s.minutes} ${tr("ui.min")}` }),
        el("div", { class: "item__sub", text: [s.owner_name, s.place].filter(Boolean).join(" · ") })),
      s.is_owner ? el("span", { class: "hint", text: s.taken ? s.taken_name || tr("ui.tc.slot_taken") : tr("ui.tc.slot_free") })
        : s.mine ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.hub.unbook"), onclick: async () => {
          try { await api(`/api/slots/${s.id}/book`, { method: "POST", body: { cancel: true } }); reload(); } catch (e) { toast(e.message); }
        } }) : s.taken ? el("span", { class: "hint", text: tr("ui.tc.slot_taken") }) : el("button", { class: "btn btn--primary btn--small", type: "button", text: tr("ui.hub.book"), onclick: async () => {
          try { await api(`/api/slots/${s.id}/book`, { method: "POST", body: {} }); haptic("success"); toast(tr("ui.hub.booked")); reload(); } catch (e) { toast(e.message); }
        } })))));
  }
  if (feature("subject_reviews") && h.kind === "discipline") {
    const sum = h.review_summary;
    blocks.push(sectionHead(`${EMOJI.reviews} ${tr("ui.hub.reviews")}`),
      el("p", { class: "hint", text: sum && sum.visible ? tr("ui.hub.review_avg", { load: sum.load, difficulty: sum.difficulty, n: sum.count }) : tr("ui.hub.review_hidden", { n: sum ? sum.count : 0, need: sum ? sum.min : 5 }) }),
      h.reviews.length ? el("div", { class: "rows" }, h.reviews.map(postRow)) : null,
      el("details", { class: "card" }, el("summary", { text: `＋ ${tr("ui.hub.add_review")}` }), postForm("review", { hubId: h.id, onDone: () => setTimeout(reload, 500) })));
  }
  if (h.mentors.length || h.teachers.length) {
    blocks.push(sectionHead(`${EMOJI.groups} ${tr("ui.hub.people")}`), el("section", { class: "card stack" },
      h.teachers.length ? el("p", {}, el("b", { text: tr("ui.hub.teachers") + ": " }), h.teachers.map((x) => x.name).join(", ")) : null,
      h.mentors.length ? el("p", {}, el("b", { text: tr("ui.hub.mentors") + ": " }), h.mentors.map((x) => x.name).join(", ")) : null));
  }
  return blocks;
}

/** Кабинет дисциплины: план курса с картой нагрузки, объявление с подтверждением, сводка по предмету, пульс группы. */
function teacherCabinet(h, reload) {
  const title = input({ maxlength: 256, placeholder: tr("ui.tc.deadline_ph") });
  const date = input({ type: "date" });
  const time = input({ type: "time" });
  const load = el("div", {});
  date.addEventListener("change", async () => {
    if (!date.value) return;
    try {
      const m = await api(`/api/hubs/${h.id}/load?date=${date.value}`);
      load.replaceChildren(el("div", { class: `card traffic traffic--${m.level}` }, el("b", { text: m.text }),
        m.items.length ? el("ul", { class: "do-list" }, m.items.map((x) => el("li", { text: `${x.hub}: ${x.title} — ${formatDay(x.due_date)}` }))) : null,
        el("p", { class: "hint", text: tr("ui.tc.load_hint") })));
    } catch (e) { load.replaceChildren(el("p", { class: "hint", text: e.message })); }
  });
  const aTitle = input({ maxlength: 256 });
  const aText = textarea({ maxlength: 4000, rows: 3 });
  const summary = el("div", { class: "stack" });
  api(`/api/hubs/${h.id}/summary`).then((s) => summary.replaceChildren(
    el("p", { text: tr("ui.tc.summary_line", { q: s.questions, faq: s.faq }) }),
    s.topics.length ? el("p", {}, el("b", { text: tr("ui.tc.topics") + " " }), s.topics.map((x) => `${x.word} (${x.count})`).join(", ")) : null,
    s.unanswered.length ? el("div", {}, el("b", { text: tr("ui.tc.unanswered") }), el("ul", { class: "do-list" }, s.unanswered.map((x) => el("li", { text: x })))) : null,
    el("p", { class: "hint", text: tr("ui.tc.summary_hint") }))).catch(() => {});
  const pulseOn = el("input", { type: "checkbox", checked: h.settings.group_pulse ? true : undefined, onchange: async (e) => {
    try { await api(`/api/hubs/${h.id}/settings`, { method: "PATCH", body: { group_pulse: e.target.checked } }); reload(); } catch (err) { toast(err.message); }
  } });
  return [sectionHead(`${EMOJI.teacher} ${tr("ui.tc.cabinet")}`), el("section", { class: "card" },
    feature("course_plan") ? numbered(1, tr("ui.tc.course_plan"), el("p", { text: tr("ui.tc.course_plan_sub") }), el("div", { class: "stack" }, title,
      el("div", { class: "row" }, date, time), load,
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.tc.publish"), onclick: async () => {
        try { const r = await api(`/api/hubs/${h.id}/deadlines`, { method: "POST", body: { title: title.value, due_date: date.value, due_time: time.value } }); haptic("success"); toast(tr("ui.tc.published", { n: r.suggested })); reload(); }
        catch (e) { toast(e.message); }
      } }))) : null,
    numbered(2, tr("ui.tc.announce"), el("p", { text: tr("ui.tc.announce_sub") }), el("div", { class: "stack" }, field(tr("ui.cm.f_title"), aTitle), field(tr("ui.cm.f_body"), aText),
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.tc.announce_send"), onclick: async () => {
        try { await api(`/api/hubs/${h.id}/announce`, { method: "POST", body: { title: aTitle.value, text: aText.value } }); haptic("success"); reload(); } catch (e) { toast(e.message); }
      } }))),
    numbered(3, tr("ui.tc.summary"), summary),
    feature("group_pulse") ? numbered(4, tr("ui.tc.group_pulse"), el("label", { class: "toggle" }, el("span", { text: tr("ui.tc.group_pulse_on") }), pulseOn),
      h.pulse_history.length ? el("div", { class: "stack" }, h.pulse_history.map((w) => el("div", { class: "row between" }, el("span", { text: w.week }),
        el("b", { text: w.average != null ? `${w.average} / 5` : tr("ui.tc.pulse_hidden", { n: w.answers }) })))) : el("p", { class: "hint", text: tr("ui.tc.pulse_hint") })) : null)];
}
