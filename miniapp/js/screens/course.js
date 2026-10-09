/* 🎓 Курс — что важно на моём курсе (1–4, магистратура).
   #course=N — открыть другой курс. На экране: кольцо чек-листа, ✅ чек-лист курса (любой пункт — в план),
   что ждёт в этом году (со статусом проверки и официальным источником), 🗓 ближайшие даты академкалендаря,
   📈 GPA и пороги перевода (#gpa), 🧩 квиз первокурсника (#quiz). */
import { api, el, tr, state, toast, haptic, skeleton, errorState, openLink } from "../core.js";
import { screenHead, sectionHead, numbered, moreButton, eventRow, linkCard, ring, EMOJI } from "../ui.js";
import { feature } from "../campus_ui.js";

function checklistBlock(c, reload) {
  return el("section", { class: "card" }, c.items.map((item) => el("label", { class: "check-row" },
    el("input", { type: "checkbox", checked: item.done ? true : undefined, onchange: async (e) => {
      try {
        await api(`/api/checklist/${item.key}?course=${c.course}`, { method: "POST", body: { done: e.target.checked } });
        haptic(e.target.checked ? "success" : "light");
        reload();
      } catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    } }),
    el("span", { class: "check-row__body" }, el("span", { class: "check-row__title", text: item.title }), el("span", { class: "hint", text: item.text }),
      feature("planner") && !item.done ? el("button", { class: "crosslink", style: "border:0;background:none;padding:4px 0 0;cursor:pointer;justify-self:start", type: "button",
        text: tr("ui2.course.to_plan"), onclick: async (e) => {
          e.preventDefault();
          try { await api(`/api/checklist/${item.key}/to-plan?course=${c.course}`, { method: "POST" }); haptic("success"); toast(tr("ui2.course.added")); } catch (err) { toast(err.message); }
        } }) : null))));
}

export async function render(view, param, ctx) {
  const head = screenHead(EMOJI.course, tr("ui2.tab.course"), tr("ui2.course.line"));
  view.replaceChildren(head, el("div", { class: "skeleton skeleton--block" }), ...skeleton(3));
  const me = state.me || {};
  const course = param || (me.role === "student" ? me.course : "") || "";
  const reload = () => render(view, param, ctx);
  let data;
  let check = null;
  try {
    [data, check] = await Promise.all([
      api(`/api/navigator${course ? "?course=" + encodeURIComponent(course) : ""}`),
      feature("checklist") ? api(`/api/checklist${course ? "?course=" + encodeURIComponent(course) : ""}`).catch(() => null) : null,
    ]);
  } catch (e) { view.replaceChildren(head, errorState(e.message, reload)); return; }

  const chips = el("div", { class: "chips", role: "group", "aria-label": tr("ui2.course.pick") }, data.courses.map((c) =>
    el("a", { class: "chip" + (c.id === data.course ? " active" : ""), href: `#course=${c.id}`, "aria-pressed": String(c.id === data.course) }, c.title)));

  const percent = check && check.total ? Math.round((100 * check.done) / check.total) : 0;
  const summary = el("section", { class: "card progress-card" },
    ring(percent, { size: 76, stroke: 8, label: check ? `${check.done}/${check.total}` : "—", small: tr("ui2.course.ring_small") }),
    el("div", { class: "progress-card__body" },
      el("div", { class: "progress-card__title", text: check ? check.title : data.lead }),
      el("div", { class: "hint", text: data.lead }),
      me.role === "student" && !me.course ? el("a", { class: "crosslink", href: "#onboarding", text: tr("ui2.course.set_course") }) : null));

  const blocks = [];
  let n = 0;
  if (check && check.items.length) blocks.push(sectionHead(`${EMOJI.checklist} ${tr("ui2.course.checklist")}`, null, null, ++n), checklistBlock(check, reload));

  // Подробности по каждому пункту (статус проверки, даты, куда идти, источник). Если есть чек-лист — те же пункты,
  // поэтому сворачиваем, чтобы не повторять список дважды.
  const details = el("div", {}, data.items.map((item, i) => numbered(i + 1, item.title,
      el("div", { class: `status-label status-label--${item.status}`, text: item.status_label }),
      el("p", { text: item.text }),
      item.events.length ? el("div", {}, item.events.slice(0, 2).map(eventRow)) : null,
      item.services.length ? el("div", { class: "chips" }, item.services.map((s) => el("a", { class: "chip", href: "#services=" + s.id }, `${EMOJI.services} ${s.title}`))) : null,
      el("div", { class: "row" }, moreButton(item.source)))));
  if (check && check.items.length) blocks.push(el("details", { class: "card" }, el("summary", { text: `${EMOJI.rules} ${tr("ui2.course.details")}` }), details));
  else blocks.push(sectionHead(`${EMOJI.course} ${tr("ui2.course.important")}`, null, null, ++n), details);

  if (data.upcoming.length) {
    blocks.push(sectionHead(`${EMOJI.dates} ${tr("ui.nav_screen.upcoming")}`, null, null, ++n),
      el("section", { class: "card" }, data.upcoming.map(eventRow), el("div", { class: "row", style: "margin-top:10px" }, moreButton(data.calendar_page, tr("ui.nav_screen.calendar")))),
      el("p", { class: "hint", text: tr("ui2.course.dates_in_plan") }));
  }

  blocks.push(sectionHead(tr("ui2.course.more")), el("div", { class: "menu-list" },
    feature("gpa") && me.role !== "teacher" ? linkCard({ emoji: EMOJI.gpa, title: tr("ui2.course.gpa"), sub: tr("ui2.course.gpa_sub"), href: "#gpa" }) : null,
    feature("quiz") && (data.course === "1" || !data.course) ? linkCard({ emoji: EMOJI.quiz, title: tr("ui2.course.quiz"), sub: tr("ui2.course.quiz_sub"), href: "#quiz" }) : null,
    linkCard({ emoji: EMOJI.plan, title: tr("ui2.course.semester_plan"), sub: tr("ui2.course.semester_plan_sub"), href: "#plan=semester" })));

  view.replaceChildren(head, chips, summary, ...blocks,
    el("p", { class: "hint", text: `${tr("ui.nav_screen.unconfirmed_note")} ${tr("ui.checked", { date: data.checked })}` }));
}

/** 🧩 Квиз первокурсника: пять вопросов о правилах МУИТ с пояснениями и источником. */
export async function renderQuiz(view, _param, ctx) {
  const head = screenHead(EMOJI.quiz, tr("ui2.course.quiz"), tr("ui2.course.quiz_line"));
  view.replaceChildren(head, ...skeleton(2));
  let q;
  try { q = await api("/api/quiz"); } catch (e) { view.replaceChildren(head, errorState(e.message, () => renderQuiz(view, _param, ctx))); return; }
  let i = 0;
  let score = 0;
  const box = el("section", { class: "card stack" });
  const step = () => {
    if (i >= q.questions.length) {
      api("/api/quiz/score", { method: "POST", body: { score } }).catch(() => {});
      haptic("success");
      box.replaceChildren(ring(Math.round((100 * score) / q.questions.length), { size: 96, stroke: 9, label: `${score}/${q.questions.length}` }),
        el("p", { text: tr("ui.path.quiz_result") }),
        el("a", { class: "btn btn--primary btn--block", href: "#course", text: tr("ui2.course.back_course") }));
      box.style.justifyItems = "center";
      return;
    }
    const item = q.questions[i];
    box.replaceChildren(el("div", { class: "bar" }, el("span", { style: `width:${Math.round((100 * i) / q.questions.length)}%` })),
      el("span", { class: "hint", text: tr("ui.path.quiz_n", { i: i + 1, n: q.questions.length }) }), el("h3", { text: item.q }),
      ...item.options.map((o, idx) => el("button", { class: "option", type: "button", text: o, onclick: (e) => {
        const right = idx === item.correct;
        if (right) score += 1;
        haptic(right ? "success" : "warning");
        box.querySelectorAll(".option").forEach((b, j) => { b.disabled = true; if (j === item.correct) b.classList.add("option--right"); });
        if (!right) e.currentTarget.classList.add("option--wrong");
        box.append(el("p", { class: "hint", text: item.why }), el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.st.next"), onclick: () => { i += 1; step(); } }));
      } })));
  };
  step();
  view.replaceChildren(head, box, el("p", { class: "hint" }, el("a", { href: q.source_url, onclick: (e) => { e.preventDefault(); openLink(q.source_url); }, text: `${tr("ui.source")}: ${q.source_title}` })));
}
