/* 📚 Предметы.
   #subjects     — мои предметы (из силлабусов и страниц дисциплин) с кольцами прогресса и кольцо семестра;
   #subject=KEY  — всё о предмете на одной странице: 📄 силлабус → 🧮 как считается оценка → ⏰ дедлайны (они уже в плане)
                   → 📁 материалы → ❓ вопросы → 🤝 честные правила. KEY: h<id> — страница дисциплины, s<id> — только силлабус.
   #sy=ID        — старая ссылка на силлабус: открываем его предмет. */
import { api, el, tr, toast, haptic, skeleton, errorState, emptyState, ask, openLink } from "../core.js";
import { screenHead, sectionHead, linkCard, ring, EMOJI, shortDay, daysLeft } from "../ui.js";
import { feature, select, isTeacher } from "../campus_ui.js";
import { deadlineRow } from "./today.js";
import { hubBlocks } from "./hubs.js";
import { postForm, postRow } from "./community.js";

function subjectCard(s) {
  const next = s.next;
  return el("a", { class: "subject-card", href: `#subject=${s.key}` },
    ring(s.percent, { size: 56, stroke: 6, label: s.tasks_total ? `${s.percent}%` : "—" }),
    el("div", { class: "subject-card__body" },
      el("div", { class: "subject-card__title", text: s.title }),
      el("div", { class: "subject-card__sub", text: [s.syllabus_id ? tr("ui2.subj.has_syllabus") : tr("ui2.subj.no_syllabus"),
        s.tasks_total ? tr("ui2.subj.tasks_n", { done: s.tasks_done, total: s.tasks_total }) : ""].filter(Boolean).join(" · ") }),
      next ? el("div", { class: next.overdue ? "subject-card__next subject-card__next--over" : "subject-card__next",
        text: `${tr("ui2.subj.next")}: ${next.title} — ${shortDay(next.due_date)} (${daysLeft(next.due_date)})` }) : null),
    el("span", { class: "arrow", text: "›", "aria-hidden": "true" }));
}

/** Кольцо семестра: неделя N из M по академкалендарю. Используется в «Предметах» и «Плане». */
export function semesterCard(sem, { link = true } = {}) {
  if (!sem) return null;
  const title = sem.week ? tr("ui2.sem.week", { week: sem.week, weeks: sem.weeks }) : tr("ui2.sem.starts", { n: sem.starts_in_days });
  return el("section", { class: "card progress-card" },
    ring(sem.percent, { size: 72, stroke: 8, small: tr("ui2.sem.short") }),
    el("div", { class: "progress-card__body" }, el("div", { class: "progress-card__title", text: title }),
      el("div", { class: "hint", text: sem.title }),
      link ? el("a", { class: "crosslink", href: "#plan=semester", text: tr("ui2.sem.to_plan") }) : null));
}

export async function render(view, _param, ctx) {
  const head = screenHead(EMOJI.subjects, tr("ui2.tab.subjects"), tr("ui2.subj.line"));
  view.replaceChildren(head, ...skeleton(3));
  let data;
  try { data = await api("/api/subjects"); } catch (e) { view.replaceChildren(head, errorState(e.message, () => render(view, _param, ctx))); return; }
  const upload = feature("syllabus") ? linkCard({ emoji: EMOJI.syllabus, title: tr("ui2.subj.upload"), sub: tr("ui2.subj.upload_sub"), href: "#syllabus", hero: true }) : null;
  const list = data.items.length ? el("div", { class: "rows" }, data.items.map(subjectCard))
    : emptyState(EMOJI.subjects, tr("ui2.subj.empty"), feature("syllabus") ? el("a", { class: "btn btn--primary btn--small", href: "#syllabus", text: tr("ui2.subj.upload") }) : null,
      tr("ui2.subj.empty_title"));
  view.replaceChildren(head, upload, semesterCard(data.semester),
    sectionHead(`${EMOJI.subjects} ${tr("ui2.subj.mine")}`), list,
    sectionHead(`${EMOJI.honesty} ${tr("ui2.subj.fair")}`),
    el("div", { class: "menu-list" },
      linkCard({ emoji: EMOJI.grade, title: tr("ui2.honesty.grade_title"), sub: tr("ui2.honesty.grade_sub"), href: "#honesty=grade" }),
      linkCard({ emoji: EMOJI.honesty, title: tr("ui2.honesty.title"), sub: tr("ui2.honesty.sub"), href: "#honesty" }),
      feature("hubs") ? linkCard({ emoji: EMOJI.topics, title: tr("ui2.subj.find_page"), sub: tr("ui2.subj.find_page_sub"), href: "#hubs" }) : null,
      isTeacher() ? linkCard({ emoji: EMOJI.teacher, title: tr("ui2.teach.title"), sub: tr("ui2.teach.line"), href: "#teach" }) : null));
}

// ------------------------------------------------------------------ страница предмета

/** «20%» → 20; «двадцать» → null. Для полосок весов из силлабуса. */
function percentOf(text) {
  const m = /(\d{1,3})\s*%/.exec(text || "");
  return m ? Math.min(100, Number(m[1])) : null;
}

function syllabusBlock(s, item, reload, ctx) {
  if (!s) {
    return el("div", { class: "empty-inline" }, el("p", { text: tr("ui2.subj.sy_none") }),
      el("button", { class: "btn btn--primary btn--small", type: "button", text: tr("ui2.subj.upload"), onclick: () => { ctx.cache.syllabusHub = item.hub_id; ctx.nav("#syllabus"); } }));
  }
  const c = s.card;
  const list = (key, items, cls = "plain-list") => (items && items.length ? el("div", {}, el("h3", { text: tr(key) }), el("ul", { class: cls }, items.map((x) => el("li", { text: x })))) : null);
  return el("section", { class: "card stack" },
    el("div", { class: "row between" }, el("b", { text: `${EMOJI.syllabus} ${c.course || s.title}` }),
      s.dated ? el("span", { class: "tag tag--ok", text: tr("ui2.subj.in_plan", { n: s.dated }) }) : el("span", { class: "tag", text: tr("ui2.subj.no_dates") })),
    el("p", { class: "hint", text: tr("ui2.subj.sy_found", { d: c.deadlines.length, g: c.grading.length }) }),
    el("div", { class: "row" },
      s.dated ? el("a", { class: "crosslink", href: "#plan=semester", text: tr("ui2.subj.to_plan") }) : null,
      c.grading.length ? el("a", { class: "crosslink", href: "#grade", onclick: (e) => { e.preventDefault(); document.getElementById("grade")?.scrollIntoView({ behavior: "smooth" }); }, text: tr("ui2.subj.to_grade") }) : null),
    el("details", {}, el("summary", { text: tr("ui2.subj.sy_details") }),
      el("div", { class: "stack" }, list("ui.st.sy_retake", c.retake_rules), list("ui.st.sy_absence", c.absence_rules),
        list("ui.st.sy_ai", c.ai_policy), c.missing.length ? el("div", { class: "callout" }, el("b", { text: tr("ui.st.sy_missing") }),
          el("ul", { class: "plain-list" }, c.missing.map((x) => el("li", { text: x })))) : null,
        el("p", { class: "disclaimer", text: tr("ui.st.sy_disclaimer") }))),
    el("div", { class: "actions" },
      el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui2.subj.sy_replace"), onclick: () => { ctx.cache.syllabusHub = item.hub_id; ctx.nav("#syllabus"); } }),
      el("button", { class: "btn btn--ghost btn--small", type: "button", text: tr("ui.sy.delete"), onclick: async () => {
        if (!(await ask(tr("ui.sy.delete_confirm")))) return;
        try { await api(`/api/syllabi/${s.id}`, { method: "DELETE" }); toast(tr("ui2.subj.sy_deleted")); item.hub_id ? reload() : ctx.nav("#subjects"); } catch (e) { toast(e.message); }
      } })));
}

/** Как считается оценка: формула МУИТ + веса из силлабуса + «сколько нужно на экзамене». */
function gradeBlock(item, s) {
  const g = item.grading;
  // Из чего складывается оценка: веса контрольных точек из силлабуса + строки про оценивание (экзамен и т. п.).
  const weights = s ? [...s.card.deadlines.filter((d) => d.weight).map((d) => `${d.title} — ${d.weight}`), ...s.card.grading] : [];
  const r1 = el("input", { class: "input", type: "number", min: 0, max: 100, step: "0.1", inputmode: "decimal", placeholder: "70", "aria-label": tr("ui.st.r1") });
  const r2 = el("input", { class: "input", type: "number", min: 0, max: 100, step: "0.1", inputmode: "decimal", placeholder: "74", "aria-label": tr("ui.st.r2") });
  const out = el("div", { class: "stack" });
  const calc = async () => {
    if (r1.value === "" || r2.value === "") { out.replaceChildren(); return; }
    try {
      const r = await api("/api/gpa/final", { method: "POST", body: { r1: Number(r1.value), r2: Number(r2.value), target: g.pass_total_percent } });
      if (!r.admitted) { out.replaceChildren(el("div", { class: "callout callout--error" }, el("b", { text: tr("ui.st.gpa_not_admitted", { adm: r.admission, min: r.admission_min }) }))); return; }
      out.replaceChildren(el("div", { class: "answer-box" }, el("b", { text: tr("ui2.grade.admission", { adm: r.admission }) }),
        el("div", { text: tr("ui.st.gpa_pass", { need: r.need_for_pass, pass: r.pass_total }) })),
      r.letters && r.letters.length ? el("div", { class: "chips" }, r.letters.slice(0, 6).map((l) => el("span", { class: "tag tag--accent", text: `${l.letter} — ${l.need}%` }))) : null,
      el("p", { class: "hint", text: tr("ui2.grade.letters_hint") }));
    } catch (e) { toast(e.message); }
  };
  [r1, r2].forEach((n) => n.addEventListener("input", calc));
  return el("section", { class: "card stack", id: "grade" },
    el("div", { class: "formula" }, el("span", { class: "hint", text: tr("ui2.grade.formula_title") }),
      el("div", { class: "formula__eq", text: tr("ui2.grade.formula") }),
      el("div", { class: "hint", text: g.admission_note })),
    weights.length ? el("div", { class: "stack" }, el("h3", { text: tr("ui2.grade.from_syllabus") }),
      el("div", { class: "weights" }, weights.map((w) => {
        const p = percentOf(w);
        return el("div", { class: "weights__row" }, el("span", { text: w }), p != null ? el("b", { text: `${p}%` }) : el("span"),
          p != null ? el("div", { class: "bar" }, el("span", { style: `width:${p}%` })) : null);
      }))) : el("p", { class: "hint", text: s ? tr("ui2.grade.no_weights") : tr("ui2.grade.need_syllabus") }),
    el("h3", { text: tr("ui2.grade.calc") }),
    el("div", { class: "row" }, el("label", { class: "field" }, el("span", { text: tr("ui.st.r1") }), r1), el("label", { class: "field" }, el("span", { text: tr("ui.st.r2") }), r2)),
    out,
    el("a", { class: "crosslink", href: "#honesty=grade", text: tr("ui2.grade.scale_link") }),
    el("p", { class: "hint" }, el("a", { href: g.source_url, onclick: (e) => { e.preventDefault(); openLink(g.source_url); }, text: `${tr("ui.source")}: ${g.source_title}` })));
}

function honestyMini(s) {
  const ai = s && s.card.ai_policy.length ? s.card.ai_policy : null;
  return el("section", { class: "card stack" },
    el("ul", { class: "do-list" }, [1, 2, 3].map((i) => el("li", { text: tr(`ui2.honesty.mini_${i}`) }))),
    ai ? el("div", {}, el("b", { text: tr("ui2.honesty.ai_here") }), el("ul", { class: "plain-list" }, ai.map((x) => el("li", { text: x })))) : null,
    el("a", { class: "crosslink", href: "#honesty", text: tr("ui2.honesty.all_rules") }));
}

async function linkToHub(item, s, reload) {
  // Предмет только из силлабуса: можно привязать к общей странице дисциплины (если такие уже есть).
  let catalog;
  try { catalog = await api("/api/hubs"); } catch { return null; }
  const disciplines = catalog.courses.flatMap((g) => g.hubs);
  // Страницы дисциплин создаются по заявкам (модератор) или из data/iitu/hubs.json → discipline_hubs. Пока их нет — «Скоро появится».
  if (!disciplines.length) {
    return el("div", { class: "callout callout--soon stack" }, el("span", { class: "soon", text: tr("ui2.soon") }),
      el("p", { text: tr("ui2.subj.page_soon") }),
      el("a", { class: "crosslink", href: "#hubs=request", text: tr("ui2.subj.page_request") }));
  }
  const pick = select([["", tr("ui.sy.no_hub")], ...disciplines.map((h) => [h.id, h.title])], "");
  pick.addEventListener("change", async () => {
    if (!pick.value) return;
    try { await api(`/api/syllabi/${s.id}`, { method: "PATCH", body: { hub_id: Number(pick.value) } }); haptic("success"); toast(tr("ui.saved")); window.location.replace(`#subject=h${pick.value}`); }
    catch (e) { toast(e.message); }
  });
  return el("section", { class: "card stack" }, el("p", { class: "hint", text: tr("ui2.subj.link_hub") }), pick);
}

export async function renderOne(view, key, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--hero" }), ...skeleton(2));
  let item;
  try { item = await api(`/api/subjects/${encodeURIComponent(key)}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderOne(view, key, ctx))); return; }
  const reload = () => renderOne(view, key, ctx);
  let hub = null;
  if (item.hub_id) { try { hub = await api(`/api/hubs/${item.hub_id}`); } catch { hub = null; } }
  const s = item.syllabi[0] || null;

  const head = el("section", { class: "card progress-card" },
    ring(item.percent, { size: 76, stroke: 8, label: item.tasks_total ? `${item.percent}%` : "—", small: tr("ui2.subj.ring_small") }),
    el("div", { class: "progress-card__body" },
      el("h1", { class: "screen-head__title", style: "font-size:1.2em" }, el("span", { class: "screen-head__emoji", text: item.emoji || EMOJI.subjects }), item.title),
      el("div", { class: "hint", text: item.tasks_total ? tr("ui2.subj.tasks_n", { done: item.tasks_done, total: item.tasks_total }) : tr("ui2.subj.no_tasks") }),
      item.next ? el("div", { class: item.next.overdue ? "subject-card__next subject-card__next--over" : "subject-card__next",
        text: `${EMOJI.deadline} ${item.next.title} — ${shortDay(item.next.due_date)}` }) : null));

  const sections = [];
  let n = 0;
  const anchor = (id, emoji, title) => ({ id, emoji, title });
  const anchors = [anchor("s-sy", EMOJI.syllabus, tr("ui2.subj.a_syllabus")), anchor("grade", EMOJI.grade, tr("ui2.subj.a_grade")),
    anchor("s-dl", EMOJI.deadline, tr("ui2.subj.a_deadlines"))];

  sections.push(el("div", { id: "s-sy" }, sectionHead(`${EMOJI.syllabus} ${tr("ui2.subj.a_syllabus")}`, null, null, ++n)), syllabusBlock(s, item, reload, ctx));
  sections.push(sectionHead(`${EMOJI.grade} ${tr("ui2.subj.grade_title")}`, null, null, ++n), gradeBlock(item, s));

  const tasks = item.tasks.length ? el("div", { class: "rows" }, item.tasks.slice(0, 20).map((t) => deadlineRow(t, null, reload)))
    : el("div", { class: "empty-inline" }, el("p", { text: s ? tr("ui2.subj.no_tasks_sy") : tr("ui2.subj.no_tasks_hint") }));
  sections.push(el("div", { id: "s-dl" }, sectionHead(`${EMOJI.deadline} ${tr("ui2.subj.a_deadlines")}`, "#plan=semester", tr("ui2.subj.in_plan_link"), ++n)), tasks);
  if (hub && hub.deadlines && hub.deadlines.length) {
    sections.push(el("p", { class: "hint", text: tr("ui2.subj.course_deadlines") }), el("div", { class: "rows" }, hub.deadlines.map((d) => el("div", { class: "row-item row-item--static" },
      el("div", { class: "row-item__body" }, el("div", { class: "row-item__title", text: d.title }),
        el("div", { class: "row-item__sub", text: shortDay(d.data.due_date) + (d.data.due_time ? " " + d.data.due_time : "") }))))));
  }

  if (hub) {
    anchors.push(anchor("s-mat", EMOJI.materials, tr("ui2.subj.a_materials")), anchor("s-q", EMOJI.questions, tr("ui2.subj.a_questions")));
    sections.push(...hubBlocks(hub, reload, { startNumber: n, subject: true }));
  } else if (s) {
    anchors.push(anchor("s-mat", EMOJI.materials, tr("ui2.subj.a_materials")));
    const box = el("div", {});
    sections.push(el("div", { id: "s-mat" }, sectionHead(`${EMOJI.materials} ${tr("ui2.subj.page_title")}`, null, null, ++n)), box);
    linkToHub(item, s, reload).then((node) => node && box.replaceChildren(node));
    if (feature("ask_senior")) {
      anchors.push(anchor("s-q", EMOJI.questions, tr("ui2.subj.a_questions")));
      const qs = el("div", {});
      sections.push(el("div", { id: "s-q" }, sectionHead(`${EMOJI.questions} ${tr("ui2.subj.a_questions")}`, "#community=senior_q", tr("ui2.subj.all_questions"), ++n)), qs,
        postForm("senior_q", { onDone: () => setTimeout(reload, 500), preset: { subject: item.title } }));
      api("/api/posts?kind=senior_q&order=new").then((rows) => {
        const mineHere = rows.filter((p) => (p.data && p.data.subject) === item.title);
        qs.replaceChildren(mineHere.length ? el("div", { class: "rows" }, mineHere.map(postRow)) : el("p", { class: "hint", text: tr("ui2.subj.no_questions") }));
      }).catch(() => qs.replaceChildren());
    }
  }
  anchors.push(anchor("s-hon", EMOJI.honesty, tr("ui2.subj.a_honesty")));
  sections.push(el("div", { id: "s-hon" }, sectionHead(`${EMOJI.honesty} ${tr("ui2.subj.a_honesty")}`)), honestyMini(s));

  const chips = el("nav", { class: "anchors", "aria-label": tr("ui2.subj.anchors") }, anchors.map((a) => el("a", { class: "chip", href: `#${a.id}`,
    onclick: (e) => { e.preventDefault(); document.getElementById(a.id)?.scrollIntoView({ behavior: "smooth", block: "start" }); } }, `${a.emoji} ${a.title}`)));
  view.replaceChildren(head, chips, ...sections, el("p", { class: "disclaimer", text: tr("ui2.subj.disclaimer") }));
  if (window.location.hash.includes("#grade")) document.getElementById("grade")?.scrollIntoView();
}

/** Старая ссылка #sy=ID → предмет, в котором лежит этот силлабус. */
export async function openSyllabus(id, ctx) {
  try {
    const data = await api("/api/subjects");
    const item = data.items.find((x) => (x.syllabus_ids || []).includes(Number(id)));
    ctx.replace(item ? `#subject=${item.key}` : "#subjects");
  } catch { ctx.replace("#subjects"); }
}

export function subjectKeyFor(syllabusId, items) {
  const item = items.find((x) => (x.syllabus_ids || []).includes(Number(syllabusId)));
  return item ? item.key : null;
}

