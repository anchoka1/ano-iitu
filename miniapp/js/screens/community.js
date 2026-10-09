/* Публикации одним механизмом (раздел 💬 IITU Hub и страницы предметов): вопросы и ответы студентов, Радар разводов,
   поиск команды, потеряшки, афиша, отзывы о предметах, материалы, вопросы перед лекцией.
   У всего пользовательского — «Пожаловаться». Что идёт через модерацию — автор видит статус в «Моих обращениях»,
   а решение бот присылает сообщением. #community=KIND, #post=ID, #pulse */
import { api, el, tr, toast, haptic, skeleton, emptyState, errorState, fileToBase64, state, openLink, formatDate, apiWithConsent } from "../core.js";
import { screenHead, sectionHead, formatDay, EMOJI } from "../ui.js";
import { feature, field, input, textarea, select, reportButton, voteButton, supportBox, pendingTag } from "../campus_ui.js";

// Вид → поля формы, флаг функции, эмодзи.
export const KINDS = {
  senior_q: { flag: "ask_senior", emoji: "❓", fields: ["title:long", "course", "hub"], anon: true, answers: "senior_a" },
  radar: { flag: "scam_radar", emoji: "🚨", fields: ["title", "body"], anon: true },
  team: { flag: "team_search", emoji: "🛠", fields: ["title", "body", "team_kind"], contact: true },
  lost: { flag: "lost_found", emoji: "🧤", fields: ["lost_found", "title", "body", "place", "photo"], contact: true },
  event: { flag: "events", emoji: "🎉", fields: ["title", "body", "date", "time", "place", "club"], going: true },
  review: { flag: "subject_reviews", emoji: "⭐", fields: ["subject", "load", "difficulty", "body"], anon: true },
  resource: { flag: "resources", emoji: "📁", fields: ["title", "url", "body"], votes: true },
  vacancy: { flag: "career", emoji: "💼", fields: ["title", "body", "url"] },
  lecture: { flag: "lecture_questions", emoji: "🎤", fields: ["title", "date"], teacher: true, answers: "lecture_q" },
};

const COURSES = [["", "—"], ["1", "1 курс"], ["2", "2 курс"], ["3", "3 курс"], ["4", "4 курс"], ["master", "Магистратура"]];

function makeField(name, hub) {
  const [key, mod] = name.split(":");
  switch (key) {
    case "title": return [key, mod === "long" ? textarea({ maxlength: 256, rows: 3, placeholder: tr("ui.cm.f_question") }) : input({ maxlength: 256 }), tr(mod === "long" ? "ui.cm.f_question" : "ui.cm.f_title")];
    case "body": return [key, textarea({ maxlength: 4000, rows: 4 }), tr("ui.cm.f_body")];
    case "course": return [key, select(COURSES, state.me && state.me.role === "student" ? state.me.course : ""), tr("ui.cm.f_course")];
    case "team_kind": return [key, select([["hackathon", tr("ui.cm.t_hackathon")], ["project", tr("ui.cm.t_project")], ["startup", tr("ui.cm.t_startup")]], "hackathon"), tr("ui.cm.f_team_kind")];
    case "lost_found": return [key, select([["lost", tr("ui.cm.lost")], ["found", tr("ui.cm.found")]], "lost"), tr("ui.cm.f_lost_found")];
    case "place": return [key, input({ maxlength: 128 }), tr("ui.cm.f_place")];
    case "date": return [key, input({ type: "date" }), tr("ui.cm.f_date")];
    case "time": return [key, input({ type: "time" }), tr("ui.cm.f_time")];
    case "club": return [key, input({ maxlength: 80 }), tr("ui.cm.f_club")];
    case "subject": return hub ? null : [key, input({ maxlength: 80 }), tr("ui.cm.f_subject")];
    case "load": return [key, select([[1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"]], 3), tr("ui.cm.f_load")];
    case "difficulty": return [key, select([[1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"]], 3), tr("ui.cm.f_difficulty")];
    case "url": return [key, input({ type: "url", maxlength: 500, placeholder: "https://" }), tr("ui.cm.f_url")];
    case "photo": return [key, el("input", { type: "file", accept: "image/jpeg,image/png,image/webp" }), tr("ui.cm.f_photo")];
    case "hub": {
      // Вопрос привязывается к хабу предмета: его увидят участники хаба и менторы.
      if (hub) return null;
      const node = select([["", tr("ui.path.any_subject")]], "");
      api("/api/hubs").then((c) => {
        const seen = new Set();
        [...c.mine, ...c.courses.flatMap((g) => g.hubs)].forEach((h) => { if (!seen.has(h.id)) { seen.add(h.id); node.append(el("option", { value: h.id }, h.title)); } });
      }).catch(() => {});
      return [key, node, tr("ui.path.q_hub")];
    }
    default: return null;
  }
}

/** Форма новой публикации. onDone(post) — после отправки. */
export function postForm(kind, { hubId = null, parentId = null, onDone, preset = null } = {}) {
  const cfg = KINDS[kind] || { fields: ["title"] };
  // preset — данные, которые подставляем сами (например, предмет со страницы предмета): такие поля не спрашиваем.
  const skip = new Set(preset ? Object.keys(preset).concat(preset.subject ? ["hub"] : []) : []);
  const fields = (parentId ? ["title:long"] : cfg.fields).filter((f) => !skip.has(f.split(":")[0])).map((f) => makeField(f, hubId)).filter(Boolean);
  const result = el("div", { class: "stack" });
  const btn = el("button", { class: "btn btn--primary btn--block", type: "button", text: parentId ? tr("ui.cm.answer_send") : tr("ui.cm.send"), onclick: async () => {
    const body = { kind: parentId ? (cfg.answers || kind) : kind, title: "", body: "", data: { ...(preset || {}) }, hub_id: hubId, parent_id: parentId };
    let photo = null;
    for (const [key, node] of fields) {
      if (key === "photo") { photo = node.files[0] || null; continue; }
      const value = node.value;
      if (key === "title" || key === "body") body[key] = value;
      else if (key === "hub") body.hub_id = value ? Number(value) : hubId;
      else body.data[key] = ["load", "difficulty"].includes(key) ? Number(value) : value;
    }
    if (kind === "lost") body.title = `${body.data.lost_found === "found" ? tr("ui.cm.found") : tr("ui.cm.lost")}: ${body.title}`;
    if (kind === "review") body.title = hubId ? tr("ui.cm.review") : (body.data.subject || "");
    if (parentId && kind === "senior_q") { body.body = body.title; body.title = body.title.slice(0, 120); }
    btn.disabled = true;
    try {
      const post = await api("/api/posts", { method: "POST", body });
      if (photo) {
        if (photo.size > 2 * 1024 * 1024) toast(tr("ui.cm.photo_big"));
        else await apiWithConsent(`/api/posts/${post.id}/photo`, { method: "POST", body: { file_base64: await fileToBase64(photo), file_type: photo.type } });
      }
      haptic("success");
      // Сообщение видно, даже если список обновится. Модераторы ещё не назначены — говорим об этом честно.
      toast(!post.pending ? tr("ui2.post.sent_ok") : state.me && state.me.moderation_ready === false ? tr("ui2.post.no_moderators") : tr("ui2.post.sent_pending"));
      result.replaceChildren(el("p", { class: "hint" }, post.pending ? tr("ui.cm.sent_pending") : tr("ui.cm.sent_ok"), " ",
        post.pending ? el("a", { href: "#mine", text: tr("ui.cm.track") }) : null), supportBox(post.support));
      fields.forEach(([key, node]) => { if (key !== "course" && node.tagName !== "SELECT") node.value = ""; });
      onDone && onDone(post);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  } });
  return el("section", { class: "card stack" }, ...fields.map(([, node, label]) => field(label, node)), btn,
    el("p", { class: "hint", text: tr(cfg.anon || parentId ? "ui.cm.anon_note" : "ui.cm.public_note") }), result);
}

/** Короткая строка публикации в списке. */
export function postRow(p) {
  const d = p.data || {};
  const meta = [
    p.kind === "event" && d.date ? formatDay(d.date) + (d.time ? " " + d.time : "") : "",
    d.place, d.club, d.course ? (COURSES.find(([c]) => c === d.course) || [])[1] : "",
    p.kind === "review" ? tr("ui.cm.review_row", { load: d.load, difficulty: d.difficulty }) : "",
    p.answers !== undefined ? tr("ui.cm.answers_n", { n: p.answers }) : "",
    p.going_count !== undefined ? tr("ui.cm.going_n", { n: p.going_count }) : "",
    p.score ? `${p.score}` : "", p.author_name,
  ].filter(Boolean).join(" · ");
  return el("a", { class: "row-item", href: `#post=${p.id}` },
    el("div", { class: "row-item__body" }, el("div", { class: "row-item__title" }, p.title || (p.body || "").slice(0, 80), " ", pendingTag(p),
      p.is_teacher ? el("span", { class: "tag tag--accent", text: tr("ui.cm.teacher_answer") }) : null),
      meta ? el("div", { class: "row-item__sub", text: meta }) : null),
    el("span", { class: "arrow", text: "›", "aria-hidden": "true" }));
}

export async function renderList(view, kind, ctx) {
  const cfg = KINDS[kind];
  if (!cfg || !feature(cfg.flag)) { view.replaceChildren(emptyState("🔒", tr("ui.feature_off"))); return; }
  const titleKey = `ui.cm.k_${kind}`;
  const head = screenHead(cfg.emoji, tr(titleKey), tr(`${titleKey}_sub`));
  view.replaceChildren(head, ...skeleton(3));
  let items;
  try { items = await api(`/api/posts?kind=${kind}&order=${cfg.votes ? "top" : "new"}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderList(view, kind, ctx))); return; }
  const canPost = !cfg.teacher || (state.me && state.me.role === "teacher");
  view.replaceChildren(head,
    items.length ? el("div", { class: "rows" }, items.map(postRow)) : emptyState(cfg.emoji, tr(`ui.cm.empty_${kind}`) !== `ui.cm.empty_${kind}` ? tr(`ui.cm.empty_${kind}`) : tr("ui.cm.empty")),
    canPost ? sectionHead(tr(kind === "senior_q" ? "ui.cm.ask" : "ui.cm.new")) : null,
    canPost ? postForm(kind, { onDone: () => setTimeout(() => renderList(view, kind, ctx), 600) }) : null,
    el("p", { class: "disclaimer", text: tr("ui.cm.rules") }));
}

export async function renderPost(view, id, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let p;
  try { p = await api(`/api/posts/${id}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderPost(view, id, ctx))); return; }
  const reload = () => renderPost(view, id, ctx);
  const cfg = KINDS[p.kind] || {};
  const d = p.data || {};
  const blocks = [];
  const actions = el("div", { class: "actions" });
  if (cfg.votes || p.kind === "senior_a" || p.kind === "lecture_q") actions.append(voteButton(p, reload));
  if (p.kind === "event" && feature("events")) {
    actions.append(el("button", { class: p.going ? "btn btn--secondary" : "btn btn--primary", type: "button", text: p.going ? tr("ui.cm.not_going") : tr("ui.cm.going"), onclick: async () => {
      try { await api(`/api/posts/${id}/going`, { method: "POST", body: { on: !p.going } }); haptic("success"); toast(p.going ? tr("ui.saved") : tr("ui.cm.going_saved")); reload(); } catch (e) { toast(e.message); }
    } }));
  }
  if (p.data && p.data.url) actions.append(el("button", { class: "btn btn--secondary", type: "button", text: tr("ui.cm.open_link"), onclick: () => openLink(p.data.url) }));
  if (!p.mine) actions.append(reportButton(p.id));
  if (p.mine) {
    actions.append(el("button", { class: "btn btn--danger btn--small", type: "button", text: tr("ui.cm.delete_mine"), onclick: async () => {
      try { await api(`/api/posts/${id}`, { method: "DELETE" }); toast(tr("ui.cm.deleted")); history.back(); } catch (e) { toast(e.message); }
    } }));
  }
  if (cfg.contact && !p.mine) {
    const msg = textarea({ maxlength: 800, rows: 2, placeholder: tr("ui.cm.contact_ph") });
    blocks.push(el("section", { class: "card stack" }, el("h3", { text: tr("ui.cm.contact") }), msg,
      el("button", { class: "btn btn--primary", type: "button", text: tr("ui.cm.contact_send"), onclick: async () => {
        try { await api(`/api/posts/${id}/contact`, { method: "POST", body: { text: msg.value } }); toast(tr("ui.cm.contact_sent")); msg.value = ""; } catch (e) { toast(e.message); }
      } }), el("p", { class: "hint", text: tr("ui.cm.contact_hint") })));
  }
  if (p.kind === "ai_rules" || p.kind === "announce") {
    if (!p.acked) actions.append(el("button", { class: "btn btn--primary", type: "button", text: tr("ui.cm.ack"), onclick: async () => {
      try { await api(`/api/posts/${id}/ack`, { method: "POST" }); haptic("success"); reload(); } catch (e) { toast(e.message); }
    } }));
    if (p.mine) {
      const rep = el("div", { class: "stack" });
      blocks.push(el("section", { class: "card stack" }, el("h3", { text: tr("ui.cm.ack_report") }), rep));
      api(`/api/posts/${id}/acks`).then((r) => rep.replaceChildren(
        el("p", {}, el("b", { text: tr("ui.cm.ack_yes", { n: r.confirmed.length }) + " " }), r.confirmed.join(", ") || "—"),
        r.known_members ? el("p", {}, el("b", { text: tr("ui.cm.ack_no", { n: r.waiting.length }) + " " }), r.waiting.join(", ") || "—") : el("p", { class: "hint", text: tr("ui.cm.ack_unknown") }))).catch(() => {});
    }
  }
  // Ответы (Спроси старшекурсника) и вопросы к лекции
  if (cfg.answers) {
    const children = await api(`/api/posts?kind=${cfg.answers}&parent_id=${id}&order=top`).catch(() => []);
    blocks.push(sectionHead(tr(p.kind === "lecture" ? "ui.cm.lecture_qs" : "ui.cm.answers")),
      children.length ? el("div", { class: "list" }, children.map((a) => el("div", { class: "card stack" },
        el("div", { class: "row" }, a.is_mentor ? el("span", { class: "tag tag--accent", text: tr("ui.cm.mentor") }) : null, a.is_teacher ? el("span", { class: "tag tag--accent", text: tr("ui.cm.teacher_answer") }) : null),
        el("p", { class: "pre", text: a.body || a.title }),
        el("div", { class: "actions" }, voteButton(a, reload), a.mine ? null : reportButton(a.id),
          p.kind === "lecture" && p.mine ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.cm.hide"), onclick: async () => {
            try { await api(`/api/posts/${a.id}/hide`, { method: "POST" }); reload(); } catch (e) { toast(e.message); }
          } }) : null)))) : el("p", { class: "hint", text: tr("ui.cm.no_answers") }),
      p.status === "approved" ? postForm(p.kind, { hubId: p.hub_id, parentId: p.id, onDone: () => setTimeout(reload, 400) }) : null);
  }
  view.replaceChildren(
    el("article", { class: "card stack" },
      el("div", { class: "row" }, el("span", { class: "tag", text: `${cfg.emoji || EMOJI.hub} ${tr(`ui.cm.k_${p.kind}`)}` }), pendingTag(p), p.is_teacher ? el("span", { class: "tag tag--accent", text: tr("ui.cm.teacher_answer") }) : null),
      el("h2", { class: "verdict__title", text: p.title }),
      p.mine && p.status === "pending" ? el("a", { class: "callout", href: "#mine" }, el("b", { text: tr("ui2.post.pending_title") }), el("span", { class: "hint", text: tr("ui2.post.pending_sub") })) : null,
      p.mine && p.status === "rejected" ? el("div", { class: "callout callout--error" }, el("b", { text: tr("ui2.post.rejected") }), p.reject_reason ? el("span", { text: p.reject_reason }) : null) : null,
      p.kind === "lost" ? el("img", { class: "card-image", alt: "", src: "", hidden: true }) : null,
      p.body ? el("p", { class: "pre", text: p.body }) : null,
      p.kind === "review" ? el("p", { text: tr("ui.cm.review_row", { load: d.load, difficulty: d.difficulty }) }) : null,
      d.place ? el("p", { class: "hint", text: d.place }) : null,
      d.date ? el("p", { class: "hint", text: formatDay(d.date) + (d.time ? " " + d.time : "") }) : null,
      p.kind === "ai_rules" && d.allowed ? el("div", {}, el("h3", { text: tr("ui.tc.ai_allowed") }), el("ul", { class: "do-list" }, d.allowed.map((x) => el("li", { text: x })))) : null,
      p.kind === "ai_rules" && d.forbidden ? el("div", {}, el("h3", { text: tr("ui.tc.ai_forbidden") }), el("ul", { class: "dont-list" }, d.forbidden.map((x) => el("li", { text: x })))) : null,
      el("div", { class: "hint", text: [p.author_name, formatDate(p.created_at)].filter(Boolean).join(" · ") }),
      actions),
    ...blocks,
    el("p", { class: "disclaimer", text: tr("ui.cm.rules") }));
  if (p.kind === "lost") {
    const img = view.querySelector("img.card-image");
    api(`/api/posts/${id}/photo`, { raw: true }).then(async (res) => { img.src = URL.createObjectURL(await res.blob()); img.hidden = false; }).catch(() => {});
  }
}

// ------------------------------------------------------------------ Пульс МУИТ

function pollResults(poll) {
  if (!poll.results) return el("p", { class: "hint", text: tr("ui.cm.hidden_results", { n: poll.answers, need: poll.min_answers }) });
  if (poll.results.options) {
    return el("div", { class: "stack" }, poll.results.options.map((o) => el("div", {},
      el("div", { class: "row between" }, el("span", { text: o.text }), el("b", { text: `${o.percent}%` })),
      el("div", { class: "conf__bar" }, el("span", { style: `width:${o.percent}%` })))),
    el("p", { class: "hint", text: tr("ui.cm.answers_total", { n: poll.answers }) }));
  }
  return el("div", { class: "stack" },
    poll.results.topics.length ? el("p", {}, el("b", { text: tr("ui.tc.topics") + " " }), poll.results.topics.map((x) => `${x.word} (${x.count})`).join(", ")) : null,
    el("ul", { class: "do-list" }, poll.results.texts.map((x) => el("li", { text: x }))));
}
export { pollResults };

/** Опрос недели (Пульс МУИТ) одним блоком: вопрос и варианты или результаты. Используется в IITU Hub. */
export async function pulseBlock(onChange) {
  const p = await api("/api/pulse");
  const box = el("section", { class: "card stack" }, el("span", { class: "tag tag--accent", text: p.subject }), el("h2", { class: "verdict__title", text: p.question }));
  if (p.answered) box.append(el("p", { class: "hint", text: tr("ui.cm.answered") }), pollResults(p));
  else {
    box.append(...p.options.map((o, i) => el("button", { class: "option", type: "button", text: o, onclick: async () => {
      try { await api(`/api/polls/${p.id}/answer`, { method: "POST", body: { option: i } }); haptic("success"); toast(tr("ui2.hub.voted")); onChange && onChange(); } catch (e) { toast(e.message); }
    } })), el("p", { class: "hint", text: tr("ui.cm.anon_poll", { n: p.min_answers }) }));
  }
  const prev = p.previous ? el("section", { class: "card stack" }, el("h3", { text: tr("ui.cm.pulse_prev") + ": " + p.previous.question }), pollResults(p.previous)) : null;
  return [box, prev, el("p", { class: "disclaimer", text: tr("ui.cm.pulse_rules") })].filter(Boolean);
}

export async function renderPulse(view) {
  const head = screenHead(EMOJI.polls, tr("ui.cm.pulse"), tr("ui.cm.pulse_sub"));
  view.replaceChildren(head, ...skeleton(2));
  try { view.replaceChildren(head, ...(await pulseBlock(() => renderPulse(view)))); }
  catch (e) { view.replaceChildren(head, errorState(e.message, () => renderPulse(view))); }
}
