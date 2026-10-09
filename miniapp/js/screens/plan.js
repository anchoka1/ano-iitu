/* 📅 План — план семестра: Неделя / Семестр / Доска, быстрое добавление текстом, задачи, которые пришли сами
   (из силлабуса — сразу, из календаря и договорённостей — с подтверждением), карточка задачи (из неё — в предмет),
   напоминания, ✍️ договорённости, 🗂 командные доски, ⏱ фокус-таймер и 🏁 итоги недели.
   «Сегодня» — на экране «Учёба», чтобы не было двух одинаковых списков.
   #plan, #plan=week|semester|board|settings, #task=ID, #boards, #board=ID, #focus, #week */
import { api, el, tr, toast, haptic, ask, skeleton, emptyState, errorState, copyText, openLink } from "../core.js";
import { screenHead, sectionHead, linkCard, eventRow, formatDay, segments, EMOJI } from "../ui.js";
import { semesterCard } from "./subjects.js";
import { feature, field, input, textarea, select, dueLabel, getFile, botDeepLink } from "../campus_ui.js";

const VIEWS = [["week", "ui.pl.v_week"], ["semester", "ui.pl.v_semester"], ["board", "ui.pl.v_board"]];
const STATUS = [["todo", "ui.pl.s_todo"], ["doing", "ui.pl.s_doing"], ["done", "ui.pl.s_done"]];
const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

function taskItem(t, ctx, reload) {
  const check = el("input", { type: "checkbox", checked: t.status === "done" ? true : undefined, "aria-label": tr("ui.pl.done"), onchange: async (e) => {
    try { await api(`/api/plan/tasks/${t.id}`, { method: "PATCH", body: { status: e.target.checked ? "done" : "todo" } }); haptic(e.target.checked ? "success" : "light"); reload(); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  } });
  const badges = [t.source === "syllabus" ? EMOJI.syllabus : "", t.promise ? EMOJI.agreement : "", t.priority >= 2 ? "❗" : "", t.repeat ? "🔁" : ""].join("");
  const meta = [dueLabel(t), t.subject, t.source !== "manual" ? t.source_label : "", t.progress && t.progress[1] ? `${t.progress[0]}/${t.progress[1]}` : ""].filter(Boolean).join(" · ");
  const row = el("div", { class: "item plan-item" + (t.status === "done" ? " plan-item--done" : "") },
    el("label", { class: "plan-check" }, check),
    el("a", { class: "item__body", href: `#task=${t.id}` }, el("div", { class: "item__title", text: `${badges ? badges + " " : ""}${t.title}` }),
      el("div", { class: "item__sub", text: meta })));
  if (t.overdue) {
    row.append(el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.pl.move_tomorrow"), onclick: async () => {
      try { await api(`/api/plan/tasks/${t.id}/move`, { method: "POST", body: { days: 1 } }); toast(tr("ui.pl.moved")); reload(); } catch (e) { toast(e.message); }
    } }));
  }
  return row;
}

function list(items, ctx, reload, empty) {
  return items.length ? el("div", { class: "list" }, items.map((t) => taskItem(t, ctx, reload))) : el("p", { class: "hint", text: empty || tr("ui.pl.empty") });
}

/** Быстрое добавление: текст → разбор → карточка задачи → подтверждение в один тап. */
function quickAdd(reload) {
  const text = input({ placeholder: tr("ui.pl.quick_ph"), maxlength: 300, "aria-label": tr("ui.pl.quick_ph") });
  const preview = el("div", { class: "stack" });
  const submit = async () => {
    if (!text.value.trim()) return;
    let draft;
    try { draft = await api("/api/plan/parse", { method: "POST", body: { text: text.value } }); } catch (e) { toast(e.message); return; }
    const title = input({ value: draft.title, maxlength: 256 });
    const date = input({ type: "date", value: draft.due_date });
    const time = input({ type: "time", value: draft.due_time });
    const subject = input({ value: draft.subject, maxlength: 64, placeholder: tr("ui.pl.subject") });
    preview.replaceChildren(el("div", { class: "card stack plan-draft" },
      el("b", { text: tr("ui.pl.draft") }), field(tr("ui.pl.title_field"), title),
      el("div", { class: "row" }, field(tr("ui.pl.date"), date), field(tr("ui.pl.time"), time)), field(tr("ui.pl.subject"), subject),
      el("div", { class: "actions" },
        el("button", { class: "btn btn--primary", type: "button", text: tr("ui.pl.add"), onclick: async () => {
          try {
            await api("/api/plan/tasks", { method: "POST", body: { title: title.value, due_date: date.value, due_time: time.value, subject: subject.value, priority: draft.priority } });
            haptic("success"); toast(tr("ui.pl.added")); text.value = ""; preview.replaceChildren(); reload();
          } catch (e) { toast(e.message); }
        } }),
        el("button", { class: "btn btn--secondary", type: "button", text: tr("ui.cancel"), onclick: () => preview.replaceChildren() }))));
  };
  text.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  return el("section", { class: "card stack" }, el("div", { class: "row plan-quick" }, text,
    el("button", { class: "btn btn--primary", type: "button", text: "＋", "aria-label": tr("ui.pl.add"), onclick: submit })),
    el("p", { class: "hint", text: tr("ui.pl.quick_hint") }), preview);
}

async function suggestionsBox(reload) {
  const box = el("div", { class: "stack" });
  let items = [];
  try { items = await api("/api/plan/suggestions"); } catch { return box; }
  if (!items.length) return box;
  box.append(sectionHead(tr("ui.pl.suggested", { n: items.length })), el("p", { class: "hint", text: tr("ui.pl.suggested_hint") }),
    el("div", { class: "list" }, items.slice(0, 12).map((s) => el("div", { class: "item" },
      
      el("div", { class: "item__body" }, el("div", { class: "item__title", text: s.title }),
        el("div", { class: "item__sub", text: [s.source_label, s.due_date ? formatDay(s.due_date) + (s.due_time ? " " + s.due_time : "") : ""].filter(Boolean).join(" · ") })),
      el("div", { class: "row" },
        el("button", { class: "btn btn--primary btn--small", type: "button", text: tr("ui.pl.accept"), onclick: async () => {
          try { await api(`/api/plan/suggestions/${s.id}/accept`, { method: "POST" }); haptic("success"); reload(); } catch (e) { toast(e.message); }
        } }),
        el("button", { class: "btn btn--secondary btn--small", type: "button", text: "✖", "aria-label": tr("ui.pl.dismiss"), onclick: async () => {
          try { await api(`/api/plan/suggestions/${s.id}/dismiss`, { method: "POST" }); reload(); } catch (e) { toast(e.message); }
        } }))))));
  return box;
}

function renderWeek(data, ctx, reload) {
  const out = [];
  if (data.overdue.length) out.push(sectionHead(tr("ui.pl.overdue")), list(data.overdue, ctx, reload));
  for (const day of data.days) {
    const d = new Date(day.date + "T00:00:00");
    const label = d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
    out.push(el("h3", { class: "plan-day", text: label }), list(day.tasks, ctx, reload, "—"));
  }
  if (data.nodate.length) out.push(sectionHead(tr("ui.pl.nodate")), list(data.nodate, ctx, reload));
  return out;
}

function renderSemester(data, ctx, reload) {
  if (!data.months.length) return [emptyState(EMOJI.plan, tr("ui.pl.semester_empty"), el("a", { class: "btn btn--primary btn--small", href: "#syllabus", text: tr("ui.today.upload_syllabus") }))];
  const out = data.months.map((m) => {
    const [y, mo] = m.month.split("-").map(Number);
    return el("section", { class: "card" }, el("h3", { text: `${MONTHS[mo - 1]} ${y}` }),
      m.milestones.map(eventRow), m.tasks.length ? list(m.tasks, ctx, reload) : null);
  });
  if (data.calendar) {
    out.push(el("a", { class: "hint", href: data.calendar.url, onclick: (e) => { e.preventDefault(); openLink(data.calendar.url); } },
      `${tr("ui.source")}: ${tr("ui.nav_screen.calendar")} ${data.calendar.year} · ${tr("ui.checked", { date: data.calendar.checked })}`));
  }
  return out;
}

/** Доска «Надо / Делаю / Готово»: перетаскивание (мышь/палец) и кнопки ← → для телефона. */
export function renderBoard(columns, onMove, ctx, reload) {
  const wrap = el("div", { class: "board" });
  STATUS.forEach(([status, key], idx) => {
    const col = el("div", { class: "board__col", "data-status": status },
      el("h3", { text: `${tr(key)} · ${columns[status].length}` }));
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("board__col--over"); });
    col.addEventListener("dragleave", () => col.classList.remove("board__col--over"));
    col.addEventListener("drop", (e) => { e.preventDefault(); col.classList.remove("board__col--over"); const id = e.dataTransfer.getData("text/plain"); if (id) onMove(Number(id), status); });
    for (const t of columns[status]) {
      const card = el("div", { class: "board__card", draggable: "true" },
        el("a", { href: `#task=${t.id}`, class: "item__title", text: t.title }),
        el("div", { class: "item__sub", text: [dueLabel(t), t.assignee_name].filter(Boolean).join(" · ") }),
        el("div", { class: "row" },
          idx > 0 ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: "←", "aria-label": tr(STATUS[idx - 1][1]), onclick: () => onMove(t.id, STATUS[idx - 1][0]) }) : null,
          idx < 2 ? el("button", { class: "btn btn--secondary btn--small", type: "button", text: "→", "aria-label": tr(STATUS[idx + 1][1]), onclick: () => onMove(t.id, STATUS[idx + 1][0]) }) : null));
      card.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", String(t.id)));
      col.append(card);
    }
    wrap.append(col);
  });
  return wrap;
}

export async function render(view, param, ctx) {
  if (param === "settings") return renderSettings(view, ctx);
  const current = VIEWS.some(([v]) => v === param) ? param : (VIEWS.some(([v]) => v === ctx.cache.planView) ? ctx.cache.planView : "week");
  ctx.cache.planView = current;
  const reload = () => render(view, current, ctx);
  const switcher = segments(VIEWS.map(([v, k]) => [v, tr(k)]), current, (v) => { ctx.cache.planView = v; ctx.nav(`#plan=${v}`); });
  const content = el("div", { class: "stack" }, ...skeleton(3));
  const tools = el("div", { class: "menu-list" },
    linkCard({ emoji: EMOJI.agreement, title: tr("ui.agr.title"), sub: tr("ui2.plan.agr_sub"), href: "#agreements" }),
    feature("planner_week_verdict") ? linkCard({ emoji: EMOJI.week, title: tr("ui.pl.verdict"), sub: tr("ui2.plan.week_sub"), href: "#week" }) : null,
    feature("planner_team_board") ? linkCard({ emoji: EMOJI.boards, title: tr("ui.pl.boards"), sub: tr("ui2.plan.boards_sub"), href: "#boards" }) : null,
    feature("focus_room") ? linkCard({ emoji: EMOJI.focus, title: tr("ui.pl.focus"), sub: tr("ui2.plan.focus_sub"), href: "#focus" }) : null,
    linkCard({ emoji: EMOJI.reminders, title: tr("ui2.plan.settings"), sub: tr("ui2.plan.settings_sub"), href: "#plan=settings" }));
  const trafficBox = el("div", {});
  const suggBox = el("div", {});
  const semBox = el("div", {});
  view.replaceChildren(screenHead(EMOJI.plan, tr("ui2.tab.plan"), tr("ui2.plan.line")), semBox, quickAdd(reload), trafficBox, suggBox, switcher, content,
    sectionHead(tr("ui2.plan.tools")), tools,
    el("p", { class: "disclaimer", text: tr("ui.pl.privacy") }));
  api("/api/subjects").then((d) => { const card = semesterCard(d.semester, { link: false }); if (card) semBox.replaceChildren(card); }).catch(() => {});
  try {
    const [data, sugg] = await Promise.all([api(`/api/plan?view=${current}`), suggestionsBox(reload)]);
    suggBox.replaceChildren(sugg);
    const parts = current === "week" ? renderWeek(data, ctx, reload)
      : current === "semester" ? renderSemester(data, ctx, reload)
        : [renderBoard(data.columns, async (id, status) => {
          try { await api(`/api/plan/tasks/${id}`, { method: "PATCH", body: { status } }); haptic("light"); reload(); } catch (e) { toast(e.message); }
        }, ctx, reload)];
    content.replaceChildren(...parts);
  } catch (e) { content.replaceChildren(errorState(e.message, reload)); }
  // Светофор недели — сверху, только если следующая неделя загружена.
  if (feature("planner_traffic")) {
    api("/api/plan/traffic").then((light) => {
      if (light.level === "green") return;
      trafficBox.replaceChildren(el("section", { class: `card traffic traffic--${light.level}` },
        el("b", { text: tr("ui.pl.traffic") }), el("p", { text: light.text }),
        light.start_early.length ? el("ul", { class: "do-list" }, light.start_early.map((x) => el("li", {}, el("a", { href: `#task=${x.id}`, text: `${x.title} — ${formatDay(x.due_date)}` })))) : null));
    }).catch(() => {});
  }
}

// ------------------------------------------------------------------ карточка задачи

export async function renderTask(view, id, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let t;
  try { t = await api(`/api/plan/tasks/${id}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderTask(view, id, ctx))); return; }
  const reload = () => renderTask(view, id, ctx);
  const title = input({ value: t.title, maxlength: 256 });
  const note = textarea({ value: t.note, maxlength: 4000, rows: 3 });
  note.value = t.note;
  const date = input({ type: "date", value: t.due_date });
  const time = input({ type: "time", value: t.due_time });
  const subject = input({ value: t.subject, maxlength: 64 });
  const priority = select([[0, tr("ui.pl.p_low")], [1, tr("ui.pl.p_normal")], [2, tr("ui.pl.p_high")]], t.priority);
  const repeat = select([["", tr("ui.pl.r_none")], ["daily", tr("ui.pl.r_daily")], ["weekly", tr("ui.pl.r_weekly")]], t.repeat);
  const status = select(STATUS.map(([v, k]) => [v, tr(k)]), t.status);
  const save = el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.save"), onclick: async () => {
    try {
      await api(`/api/plan/tasks/${id}`, { method: "PATCH", body: { title: title.value, note: note.value, due_date: date.value, due_time: time.value,
        subject: subject.value, priority: Number(priority.value), repeat: repeat.value, status: status.value } });
      haptic("success"); toast(tr("ui.saved")); reload();
    } catch (e) { toast(e.message); }
  } });
  const act = (label, fn, cls = "btn btn--secondary btn--small") => el("button", { class: cls, type: "button", text: label, onclick: async () => {
    try { await fn(); } catch (e) { toast(e.message); }
  } });
  const actions = el("div", { class: "actions" },
    act(t.status === "done" ? tr("ui.pl.undone") : tr("ui.pl.done"), async () => { await api(`/api/plan/tasks/${id}`, { method: "PATCH", body: { status: t.status === "done" ? "todo" : "done" } }); haptic("success"); reload(); }, "btn btn--primary btn--small"),
    act(tr("ui.pl.move_tomorrow"), async () => { await api(`/api/plan/tasks/${id}/move`, { method: "POST", body: { days: 1 } }); toast(tr("ui.pl.moved")); reload(); }),
    act(tr("ui.pl.move_week"), async () => { await api(`/api/plan/tasks/${id}/move`, { method: "POST", body: { days: 7 } }); toast(tr("ui.pl.moved")); reload(); }),
    feature("focus_room") ? act(tr("ui.pl.pomodoro"), async () => { ctx.cache.focusTask = { id: t.id, title: t.title }; ctx.nav("#focus"); }) : null);

  const blocks = [];
  // Подзадачи и «Разбей на шаги»
  const sub = input({ placeholder: tr("ui.pl.subtask_ph"), maxlength: 200 });
  const subList = el("div", { class: "list" }, (t.subtasks || []).map((s) => el("label", { class: "toggle" },
    el("span", { text: `${s.title}${s.due_date ? " · " + formatDay(s.due_date) : ""}` }),
    el("input", { type: "checkbox", checked: s.status === "done" ? true : undefined, onchange: async (e) => {
      try { await api(`/api/plan/tasks/${s.id}`, { method: "PATCH", body: { status: e.target.checked ? "done" : "todo" } }); } catch (err) { toast(err.message); }
    } }))));
  const stepsBox = el("div", { class: "stack" });
  blocks.push(el("section", { class: "card stack" }, el("h3", { text: tr("ui.pl.subtasks") + (t.progress && t.progress[1] ? ` · ${t.progress[0]}/${t.progress[1]}` : "") }), subList,
    el("div", { class: "row plan-quick" }, sub, el("button", { class: "btn btn--secondary", type: "button", text: "＋", onclick: async () => {
      if (!sub.value.trim()) return;
      try { await api("/api/plan/tasks", { method: "POST", body: { title: sub.value, parent_id: t.id } }); reload(); } catch (e) { toast(e.message); }
    } })),
    feature("planner_steps") && !t.parent_id ? el("button", { class: "btn btn--secondary btn--block", type: "button", text: tr("ui.pl.steps"), onclick: async () => {
      stepsBox.replaceChildren(...skeleton(1));
      try {
        const { steps } = await api(`/api/plan/tasks/${id}/steps/preview`, { method: "POST" });
        const rows = steps.map((s) => ({ title: input({ value: s.title, maxlength: 200 }), date: input({ type: "date", value: s.due_date }) }));
        stepsBox.replaceChildren(el("p", { class: "hint", text: tr("ui.pl.steps_hint") }),
          ...rows.map((r) => el("div", { class: "row plan-quick" }, r.title, r.date)),
          el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.pl.steps_add"), onclick: async () => {
            try { await api(`/api/plan/tasks/${id}/steps`, { method: "POST", body: { steps: rows.map((r) => ({ title: r.title.value, due_date: r.date.value })) } }); haptic("success"); reload(); } catch (e) { toast(e.message); }
          } }));
      } catch (e) { stepsBox.replaceChildren(el("p", { class: "hint", text: e.message })); }
    } }) : null, stepsBox));

  // «Договор с собой»
  if (feature("planner_promise") && !t.board_id) {
    const box = el("section", { class: "card stack" }, el("h3", { text: tr("ui.pl.promise") }), el("p", { class: "hint", text: tr("ui.pl.promise_hint") }));
    if (t.promise) {
      box.append(el("p", { text: t.witness_state === "accepted" ? tr("ui.pl.witness_ok", { name: t.witness_name }) : t.witness_state === "invited" ? tr("ui.pl.witness_wait") : tr("ui.pl.promise_on") }));
    }
    if (!t.promise || !t.witness_state) {
      box.append(el("div", { class: "actions" },
        !t.promise ? act(tr("ui.pl.promise_make"), async () => { await api(`/api/plan/tasks/${id}/promise`, { method: "POST", body: { witness: false } }); reload(); }) : null,
        act(tr("ui.pl.promise_witness"), async () => {
          const r = await api(`/api/plan/tasks/${id}/promise`, { method: "POST", body: { witness: true } });
          const link = botDeepLink(`wit_${r.witness_token}`);
          if (link) { await copyText(link); toast(tr("ui.pl.witness_link_copied")); } else toast(tr("ui.agr.no_bot_link"));
          reload();
        })));
    }
    blocks.push(box);
  }

  const subjectLink = el("div", {});
  if (t.subject || t.source === "syllabus") {
    api("/api/subjects").then((d) => {
      const norm = (x) => (x || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
      const ref = (t.source === "syllabus" && /^sy(\d+):/.exec(t.source_ref || "")) || null;
      const item = d.items.find((x) => norm(x.title) === norm(t.subject)) || (ref ? d.items.find((x) => (x.syllabus_ids || []).includes(Number(ref[1]))) : null);
      if (item) subjectLink.replaceChildren(linkCard({ emoji: EMOJI.subjects, title: item.title, sub: tr("ui2.plan.task_subject"), href: `#subject=${item.key}` }));
    }).catch(() => {});
  }
  view.replaceChildren(screenHead(EMOJI.deadline, t.title, t.source !== "manual" ? t.source_label : tr("ui2.plan.task_line")), subjectLink,
    el("section", { class: "card stack" },
      field(tr("ui.pl.title_field"), title), field(tr("ui.pl.note"), note),
      el("div", { class: "row" }, field(tr("ui.pl.date"), date), field(tr("ui.pl.time"), time)),
      field(tr("ui.pl.subject"), subject),
      el("div", { class: "row" }, field(tr("ui.pl.priority"), priority), field(tr("ui.pl.repeat"), repeat), field(tr("ui.pl.status"), status)),
      t.focus_minutes ? el("p", { class: "hint", text: tr("ui.pl.focus_spent", { n: t.focus_minutes }) }) : null,
      save, actions),
    ...blocks,
    el("button", { class: "btn btn--danger btn--block", type: "button", text: tr("ui.pl.delete"), onclick: async () => {
      if (!(await ask(tr("ui.pl.delete_confirm")))) return;
      try { await api(`/api/plan/tasks/${id}`, { method: "DELETE" }); ctx.nav("#plan"); } catch (e) { toast(e.message); }
    } }));
}

// ------------------------------------------------------------------ настройки

async function renderSettings(view, ctx) {
  view.replaceChildren(screenHead(EMOJI.reminders, tr("ui2.plan.settings"), tr("ui2.plan.settings_sub")), ...skeleton(2));
  let s;
  try { s = await api("/api/plan/settings"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderSettings(view, ctx))); return; }
  const patch = async (body) => { try { await api("/api/plan/settings", { method: "PATCH", body }); toast(tr("ui.saved")); } catch (e) { toast(e.message); } };
  const remind = el("input", { type: "checkbox", checked: s.remind ? true : undefined, onchange: (e) => patch({ remind: e.target.checked }) });
  const offset = select([[15, tr("ui.pl.off_15")], [60, tr("ui.pl.off_60")], [180, tr("ui.pl.off_180")], [1440, tr("ui.pl.off_1440")]], s.remind_offset,
    { onchange: (e) => patch({ remind_offset: Number(e.target.value) }) });
  const [qs, qe] = s.quiet.split("-");
  const quietFrom = input({ type: "number", min: 0, max: 23, value: qs });
  const quietTo = input({ type: "number", min: 0, max: 23, value: qe });
  const subs = el("div", {}, s.subscriptions.map((sub) => el("label", { class: "toggle" }, el("span", { text: sub.title }),
    el("input", { type: "checkbox", checked: sub.on ? true : undefined, onchange: async (e) => {
      try { await api("/api/subscriptions", { method: "PATCH", body: { key: sub.key, on: e.target.checked } }); } catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    } }))));
  const traffic = el("div", {});
  if (feature("planner_traffic")) {
    api("/api/plan/traffic").then((l) => traffic.replaceChildren(el("section", { class: `card traffic traffic--${l.level}` },
      el("b", { text: tr("ui.pl.traffic") }), el("p", { text: l.text })))).catch(() => {});
  }
  view.replaceChildren(screenHead(EMOJI.reminders, tr("ui2.plan.settings"), tr("ui2.plan.settings_sub")), traffic,
    el("section", { class: "card" }, el("h3", { text: tr("ui.pl.reminders") }),
      el("label", { class: "toggle" }, el("span", { text: tr("ui.pl.remind_on") }), remind),
      el("div", { class: "toggle" }, el("span", { text: tr("ui.pl.remind_offset") }), offset),
      el("div", { class: "toggle" }, el("span", { text: tr("ui.pl.quiet") }), el("div", { class: "row" }, quietFrom, "—", quietTo,
        el("button", { class: "btn btn--secondary btn--small", type: "button", text: "OK", onclick: () => patch({ quiet: `${Number(quietFrom.value)}-${Number(quietTo.value)}` }) }))),
      el("p", { class: "hint", text: tr("ui.pl.remind_hint") })),
    el("section", { class: "card" }, el("h3", { text: tr("ui.pl.subscriptions") }), el("p", { class: "hint", text: tr("ui.pl.subs_hint") }), subs),
    el("button", { class: "btn btn--secondary btn--block", type: "button", text: tr("ui.pl.export"), onclick: () => getFile("/api/plan/export.ics", "/api/plan/export/send", "moi_plan.ics") }),
    el("button", { class: "btn btn--danger btn--block", type: "button", text: tr("ui.pl.delete_all"), onclick: async () => {
      if (!(await ask(tr("ui.pl.delete_all_confirm")))) return;
      try { await api("/api/plan", { method: "DELETE" }); toast(tr("ui.pl.deleted_all")); ctx.nav("#plan"); } catch (e) { toast(e.message); }
    } }));
}

// ------------------------------------------------------------------ командные доски

export async function renderBoards(view, _param, ctx) {
  view.replaceChildren(screenHead(EMOJI.boards, tr("ui.pl.boards"), tr("ui.pl.boards_sub")), ...skeleton(2));
  let boards, agreements, circles;
  try {
    [boards, agreements, circles] = await Promise.all([api("/api/boards"), api("/api/agreements").catch(() => []), api("/api/circles").catch(() => [])]);
  } catch (e) { view.replaceChildren(errorState(e.message, () => renderBoards(view, _param, ctx))); return; }
  const source = select([["", tr("ui.pl.board_source")],
    ...agreements.filter((a) => ["confirmed", "done"].includes(a.status)).map((a) => [`agr:${a.code}`, (a.draft.what || a.text).slice(0, 60)]),
    ...circles.map((c) => [`grp:${c.id}`, c.title])], "");
  const title = input({ placeholder: tr("ui.pl.board_title"), maxlength: 128 });
  const create = el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.pl.board_create"), onclick: async () => {
    const [kind, value] = source.value.split(":");
    if (!kind) { toast(tr("ui.pl.board_source")); return; }
    try {
      const b = await api("/api/boards", { method: "POST", body: { title: title.value, agreement_code: kind === "agr" ? value : "", circle_id: kind === "grp" ? Number(value) : null } });
      ctx.nav(`#board=${b.id}`);
    } catch (e) { toast(e.message); }
  } });
  view.replaceChildren(screenHead(EMOJI.boards, tr("ui.pl.boards"), tr("ui.pl.boards_sub")),
    boards.length ? el("div", { class: "list" }, boards.map((b) => linkCard({ title: b.title, href: `#board=${b.id}`, emoji: "🗂" }))) : emptyState("🗂", tr("ui.pl.boards_empty")),
    el("section", { class: "card stack" }, field(tr("ui.pl.board_from"), source), field(tr("ui.pl.board_title"), title), create));
}

export async function renderBoardOne(view, id, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let b;
  try { b = await api(`/api/boards/${id}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderBoardOne(view, id, ctx))); return; }
  const reload = () => renderBoardOne(view, id, ctx);
  const title = input({ placeholder: tr("ui.pl.title_field"), maxlength: 256 });
  const who = select([["", tr("ui.pl.nobody")], ...b.members.map((m) => [m.user_id, m.name])], "");
  const date = input({ type: "date" });
  view.replaceChildren(screenHead(EMOJI.boards, b.title, b.members.map((m) => m.name).join(", ")),
    el("section", { class: "card stack" }, field(tr("ui.pl.title_field"), title), el("div", { class: "row" }, field(tr("ui.pl.assignee"), who), field(tr("ui.pl.date"), date)),
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.pl.add"), onclick: async () => {
        if (!title.value.trim()) return;
        try { await api(`/api/boards/${id}/tasks`, { method: "POST", body: { title: title.value, assignee_id: who.value ? Number(who.value) : null, due_date: date.value } }); reload(); } catch (e) { toast(e.message); }
      } })),
    renderBoard(b.columns, async (taskId, status) => {
      try { await api(`/api/plan/tasks/${taskId}`, { method: "PATCH", body: { status } }); reload(); } catch (e) { toast(e.message); }
    }, ctx, reload),
    el("p", { class: "hint", text: tr("ui.pl.board_hint") }));
}

// ------------------------------------------------------------------ «Помидор» и «Фокус-комната»

let focusTimer = null;

export async function renderFocus(view, _param, ctx) {
  clearInterval(focusTimer);
  view.replaceChildren(screenHead(EMOJI.focus, tr("ui.pl.focus"), tr("ui.pl.focus_sub")), ...skeleton(1));
  let room;
  try { room = await api("/api/focus/room"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderFocus(view, _param, ctx))); return; }
  const task = ctx.cache.focusTask;
  const clock = el("div", { class: "focus__clock", text: "25:00" });
  const people = el("p", { class: "focus__room", text: tr("ui.pl.focus_now", { n: room.now }) });
  const minutes = select([[25, "25"], [15, "15"], [45, "45"], [50, "50"]], 25);
  const tick = (endsAt, sessionId) => {
    clearInterval(focusTimer);
    const update = async () => {
      if (!clock.isConnected) { clearInterval(focusTimer); return; }
      const left = Math.max(0, Math.round((new Date(endsAt) - new Date()) / 1000));
      clock.textContent = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
      if (left <= 0) {
        clearInterval(focusTimer);
        try { await api(`/api/focus/${sessionId}/finish`, { method: "POST" }); } catch { /* сессия уже закрыта */ }
        haptic("success"); toast(tr("ui.pl.focus_done")); ctx.cache.focusTask = null; renderFocus(view, _param, ctx);
      }
    };
    update();
    focusTimer = setInterval(update, 1000);
  };
  const controls = el("div", { class: "actions" });
  if (room.mine) {
    tick(room.mine.ends_at, room.mine.id);
    controls.append(el("button", { class: "btn btn--secondary btn--block", type: "button", text: tr("ui.pl.focus_stop"), onclick: async () => {
      try { await api(`/api/focus/${room.mine.id}/finish`, { method: "POST" }); clearInterval(focusTimer); ctx.cache.focusTask = null; renderFocus(view, _param, ctx); } catch (e) { toast(e.message); }
    } }));
  } else {
    controls.append(field(tr("ui.pl.focus_minutes"), minutes), el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.pl.focus_start"), onclick: async () => {
      try { await api("/api/focus/start", { method: "POST", body: { minutes: Number(minutes.value), task_id: task ? task.id : null } }); haptic("light"); renderFocus(view, _param, ctx); } catch (e) { toast(e.message); }
    } }));
  }
  view.replaceChildren(screenHead(EMOJI.focus, tr("ui.pl.focus"), tr("ui.pl.focus_sub")),
    el("section", { class: "card focus" }, task ? el("p", { class: "hint", text: tr("ui.pl.focus_task", { title: task.title }) }) : null, clock, people,
      el("p", { class: "hint", text: tr("ui.pl.focus_today", { n: room.today }) }), controls),
    el("p", { class: "disclaimer", text: tr("ui.pl.focus_privacy") }));
}

// ------------------------------------------------------------------ «Итоги недели»

export async function renderVerdict(view) {
  view.replaceChildren(screenHead(EMOJI.week, tr("ui.pl.verdict"), tr("ui2.plan.week_sub")), ...skeleton(2));
  let v;
  try { v = await api("/api/plan/week-verdict"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderVerdict(view))); return; }
  const block = (key, items) => items.length ? el("div", {}, el("h3", { text: tr(key) }), el("ul", { class: "do-list" }, items.map((x) => el("li", { text: x })))) : null;
  view.replaceChildren(screenHead(EMOJI.week, tr("ui.pl.verdict"), tr("ui2.plan.week_sub")),
    el("article", { class: `card verdict verdict--${v.status}` }, el("span", { class: "status-pill", text: `${formatDay(v.week_start)} — ${formatDay(v.week_end)}` }),
      el("h2", { class: "verdict__title", text: v.title }),
      block("ui.pl.vd_done", v.done), block("ui.pl.vd_moved", v.moved), block("ui.pl.vd_open", v.open), block("ui.pl.vd_hot", v.hot),
      el("p", { class: "hint", text: tr("ui.pl.vd_kind") })),
    el("button", { class: "btn btn--secondary btn--block", type: "button", text: tr("ui.pl.vd_share"), onclick: () => getFile("/api/plan/week-verdict.png", "/api/plan/week-verdict/send", "itogi_nedeli.png") }),
    el("p", { class: "disclaimer", text: tr("ui.pl.vd_private") }));
}
