/* Онбординг и смена профиля: роль → курс, факультет, программа (студент) или кафедра (преподаватель) → язык.
   Храним минимум: роль, курс, факультет/программа или кафедра, язык. ИИН и оценки не спрашиваем. */
import { api, el, tr, state, toast, haptic, skeleton, errorState } from "../core.js";
import { screenTitle, numbered, screenHead, EMOJI } from "../ui.js";

function select(label, options, value, onchange) {
  const node = el("select", { class: "select", "aria-label": label, onchange: (e) => onchange(e.target.value) },
    options.map(([v, text]) => el("option", { value: v, selected: v === value ? true : undefined }, text)));
  return el("label", { class: "field" }, el("span", { text: label }), node);
}

export async function render(view, _param, ctx) {
  view.replaceChildren(screenHead(EMOJI.profile, tr("ui.onb.title"), tr("ui.onb.sub")), ...skeleton(2));
  let ref;
  try { ref = await api("/api/iitu/reference"); } catch (e) { view.replaceChildren(errorState(e.message, () => render(view, _param, ctx))); return; }
  const me = state.me || {};
  const form = { role: me.role || "", course: me.course || "", faculty: me.faculty || "", program: me.program || "", department: me.department || "", ui_lang: "ru" };
  const box = el("div", { class: "stack" });

  const roleChips = el("div", { class: "chips", role: "group" });
  for (const [role, key] of [["student", "ui.onb.student"], ["teacher", "ui.onb.teacher"]]) {
    roleChips.append(el("button", { class: "chip", type: "button", "aria-pressed": String(form.role === role),
      onclick: () => { form.role = role; haptic("select"); paint(); } }, tr(key)));
  }
  const langChips = el("div", { class: "chips", role: "group" }, ref.languages.map((lang) => el("button", {
    class: "chip", type: "button", "aria-pressed": String(lang.id === "ru"),
    onclick: () => { if (!lang.ready) toast(tr("ui.lang_soon")); } }, lang.title + (lang.ready ? "" : " · скоро"))));

  function paint() {
    roleChips.querySelectorAll(".chip").forEach((c, i) => c.setAttribute("aria-pressed", String(["student", "teacher"][i] === form.role)));
    const parts = [];
    if (form.role === "student") {
      parts.push(select(tr("ui.onb.course"), [["", "—"], ...ref.courses.map((c) => [c.id, c.title])], form.course, (v) => { form.course = v; form.program = ""; paint(); }));
      parts.push(select(tr("ui.onb.faculty"), [["", "—"], ...ref.faculties.map((f) => [f.id, `${f.short} — ${f.title}`])], form.faculty, (v) => { form.faculty = v; form.program = ""; paint(); }));
      const isMaster = form.course === "master" || /^m\d/.test(form.course);
      const level = isMaster ? "master" : form.course === "phd" ? "phd" : "bachelor";
      const fac = ref.faculties.find((f) => f.id === form.faculty);
      if (fac) {
        parts.push(select(tr("ui.onb.program"), [["", tr("ui.onb.program_none")], ...fac.programs.filter((p) => p.level === level).map((p) => [p.code, `${p.code} ${p.title}`])],
          form.program, (v) => { form.program = v; }));
      }
    } else if (form.role === "teacher") {
      parts.push(select(tr("ui.onb.department"), [["", "—"], ...ref.departments.map((d) => [d.id, d.title])], form.department, (v) => { form.department = v; }));
      parts.push(el("p", { class: "hint", text: tr("ui.onb.teacher_note") }));
    }
    box.replaceChildren(...parts);
  }

  const save = el("button", { class: "btn btn--primary btn--block", text: tr("ui.onb.save"), onclick: async () => {
    if (!form.role) { haptic("warning"); toast(tr("ui.onb.role")); return; }
    save.disabled = true;
    try {
      const saved = await api("/api/me/profile", { method: "PATCH", body: form });
      Object.assign(state.me, saved);
      window.dispatchEvent(new Event("profilechange"));
      haptic("success");
      if (saved.changes && saved.changes.length) {
        view.replaceChildren(screenHead(EMOJI.course, saved.profile_label, tr("ui.onb.changes")),
          el("section", { class: "card" }, saved.changes.map((c, i) => numbered(i + 1, c.title, el("p", { text: c.text })))),
          el("a", { class: "btn btn--primary btn--block", href: "#course" }, `${EMOJI.course} ${tr("ui2.onb.to_course")}`));
        return;
      }
      ctx.nav("#home");
    } catch (e) { toast(e.message); }
    save.disabled = false;
  } });

  paint();
  view.replaceChildren(screenHead(EMOJI.profile, tr("ui.onb.title"), tr("ui.onb.sub")),
    el("section", { class: "card stack" },
      el("div", { class: "field" }, el("span", { text: tr("ui.onb.role") }), roleChips),
      box,
      el("div", { class: "field" }, el("span", { text: tr("ui.onb.lang") }), langChips),
      save,
      el("a", { class: "btn btn--secondary btn--block", href: "#home" }, tr("ui.onb.skip"))));
}
