/* 🎒 Учёба — главный экран: всё на сегодня.
   Сверху — одна строка, что такое ANO IITU, и главная кнопка «Проверить информацию».
   Ниже — ближайшие дедлайны (из каждого можно перейти в предмет), новое для меня (ответы, решения модератора,
   свежие предупреждения Радара), обратный отсчёт до РК и сессии, утренняя сводка. Пустые блоки не показываем. */
import { api, el, tr, state, skeleton, errorState, formatDate, toast, haptic } from "../core.js";
import { sectionHead, linkCard, EMOJI, dateBadge, daysLeft, shortDay } from "../ui.js";
import { feature, isTeacher } from "../campus_ui.js";

const norm = (s) => (s || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");

/** Строка дедлайна: дата-плашка → задача; справа — ссылка на предмет (связь «дедлайн → предмет»). */
export function deadlineRow(t, subjectKey, reload) {
  const check = el("input", { type: "checkbox", "aria-label": tr("ui.pl.done"), checked: t.status === "done" ? true : undefined, onchange: async (e) => {
    try {
      await api(`/api/plan/tasks/${t.id}`, { method: "PATCH", body: { status: e.target.checked ? "done" : "todo" } });
      haptic(e.target.checked ? "success" : "light");
      if (e.target.checked) toast(tr("ui2.today.done_toast"));
      reload && reload();
    } catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  } });
  const when = [t.due_date ? shortDay(t.due_date) : tr("ui.pl.no_date"), t.due_time, t.due_date ? daysLeft(t.due_date) : ""].filter(Boolean).join(" · ");
  return el("div", { class: `row-item${t.overdue ? " row-item--alert" : ""}${t.status === "done" ? " row-item--done" : ""}` },
    dateBadge(t.due_date, t.overdue),
    el("div", { class: "row-item__body", style: "cursor:pointer", onclick: (e) => { if (!e.target.closest("a")) window.location.hash = `#task=${t.id}`; } },
      el("a", { class: "row-item__title row-item__link", href: `#task=${t.id}`, text: t.title }),
      el("div", { class: "row-item__sub", text: when }),
      subjectKey ? el("a", { class: "crosslink", href: `#subject=${subjectKey}`, "aria-label": `${tr("ui2.today.to_subject")}: ${t.subject}` },
        `${EMOJI.subjects} ${t.subject}`) : null),
    el("label", { class: "plan-check" }, check));
}

export async function render(view, _param, ctx) {
  const me = state.me || {};
  const hero = el("section", { class: "hero" },
    el("div", { class: "hero__kicker", text: "ANO IITU" }),
    el("h1", { text: me.first_name ? tr("ui2.today.hello", { name: me.first_name }) : tr("ui2.today.hello_anon") }),
    el("p", { text: tr("ui2.today.mission") }),
    el("a", { class: "btn btn--light btn--block btn--big", href: "#verify" }, `${EMOJI.check} ${tr("ui2.check.cta")}`));
  view.replaceChildren(hero, ...skeleton(3));
  const reload = () => render(view, _param, ctx);

  let d;
  let subj = { items: [] };
  try {
    [d, subj] = await Promise.all([api("/api/today"), api("/api/subjects").catch(() => ({ items: [] }))]);
  } catch (e) { view.replaceChildren(hero, errorState(e.message, reload)); return; }
  const keyByName = new Map(subj.items.map((s) => [norm(s.title), s.key]));
  const blocks = [];

  if (!me.onboarded) {
    blocks.push(el("a", { class: "callout callout--action", href: "#onboarding" },
      el("b", { text: `${EMOJI.profile} ${tr("ui.today.profile_title")}` }), el("span", { text: tr("ui.today.profile_sub") })));
  }
  if (d.mod_queue) blocks.push(linkCard({ emoji: EMOJI.mod, title: tr("ui.today.mod_queue", { n: d.mod_queue }), sub: tr("ui.today.mod_queue_sub"), href: "#mod", badge: d.mod_queue }));
  if (isTeacher()) blocks.push(linkCard({ emoji: EMOJI.teacher, title: tr("ui2.teach.title"), sub: tr("ui2.teach.line"), href: "#teach" }));

  // ⏰ Дедлайны — главное в учёбе. Пусто — подсказываем, откуда они берутся.
  if (feature("planner")) {
    const items = (d.deadlines || []).map((t) => deadlineRow(t, keyByName.get(norm(t.subject)), reload));
    blocks.push(sectionHead(`${EMOJI.deadline} ${tr("ui2.today.deadlines")}`, "#plan", tr("ui2.today.all_plan")));
    if (items.length) blocks.push(el("div", { class: "rows" }, items));
    else {
      blocks.push(el("div", { class: "empty" }, el("div", { class: "empty__icon", text: EMOJI.syllabus, "aria-hidden": "true" }),
        el("p", { class: "empty__title", text: tr("ui2.today.no_deadlines_title") }), el("p", { text: tr("ui2.today.no_deadlines") }),
        el("a", { class: "btn btn--primary btn--small", href: "#syllabus", text: tr("ui.today.upload_syllabus") })));
    }
    if (d.suggestions) blocks.push(linkCard({ emoji: EMOJI.plan, title: tr("ui.today.suggested", { n: d.suggestions }), sub: tr("ui.today.suggested_sub"), href: "#plan", badge: d.suggestions }));
  }

  // 🗓 Обратный отсчёт по академкалендарю
  if (d.countdown && d.countdown.length) {
    blocks.push(sectionHead(`${EMOJI.dates} ${tr("ui2.today.soon")}`, "#course", tr("ui2.today.all_dates")),
      el("div", { class: "countdown" }, d.countdown.map((c) => el("a", { class: "countdown__item", href: "#course", title: `${c.title} · ${c.dates}` },
        dateBadge(c.start),
        el("div", {}, el("b", { text: c.ongoing ? tr("ui.home.now") : tr("ui.today.days", { n: c.days }) }), el("small", { text: c.label }))))));
  }

  // 🔔 Новое для тебя: ответы на вопросы, решения по обращениям, свежие схемы из Радара
  const news = [];
  for (const a of d.answers || []) news.push(linkCard({ emoji: EMOJI.questions, title: tr("ui.today.answers"), sub: `«${a.question}» — ${a.answer}`, href: `#post=${a.question_id}` }));
  for (const x of d.decisions || []) {
    news.push(linkCard({ emoji: EMOJI.mine, title: `${x.kind_label}: ${x.status_label}`, href: "#mine",
      sub: x.reason ? tr("ui.mine.reason", { reason: x.reason }) : (x.title || formatDate(x.when)) }));
  }
  for (const r of d.radar || []) news.push(linkCard({ emoji: EMOJI.radar, title: r.title, sub: tr("ui2.today.radar_sub"), href: `#post=${r.id}` }));
  if (news.length) blocks.push(sectionHead(`${EMOJI.reminders} ${tr("ui2.today.new")}`), el("div", { class: "menu-list" }, news.slice(0, 4)));

  // Утренняя сводка — напоминание в бот
  if (feature("morning_digest")) {
    const box = el("div", {});
    blocks.push(box);
    api("/api/morning").then((m) => box.replaceChildren(el("label", { class: "toggle card" },
      el("span", {}, el("b", { text: `${EMOJI.reminders} ${tr("ui2.today.morning")}` }), el("br"), el("span", { class: "hint", text: tr("ui2.today.morning_sub") })),
      el("input", { type: "checkbox", checked: m.subscribed ? true : undefined, onchange: async (e) => {
        try { await api("/api/subscriptions", { method: "PATCH", body: { key: "sub.morning", on: e.target.checked } }); toast(tr("ui.saved")); }
        catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
      } })))).catch(() => box.remove());
  }

  blocks.push(el("p", { class: "disclaimer", text: tr("ui2.today.honest_note") }));
  view.replaceChildren(hero, ...blocks);
}
