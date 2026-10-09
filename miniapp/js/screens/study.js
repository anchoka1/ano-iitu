/* Два экрана с расчётами и черновиками:
   #gpa     (🎓 Курс)      — 📈 GPA за период и пороги перевода на следующий курс (оценка по предмету считается на странице предмета);
   #appeals (🧭 Навигатор) — 📝 заявления и обращения: черновик, куда нести и в какой срок; проверка письма или заявления. */
import { api, el, tr, toast, skeleton, errorState, copyText, openLink, state, apiWithConsent, filePicker, fileToBase64 } from "../core.js";
import { sectionHead, numbered, screenHead, EMOJI } from "../ui.js";
import { field, input, textarea, select, supportBox } from "../campus_ui.js";

// ------------------------------------------------------------------ GPA

export async function renderGpa(view) {
  let rules;
  try { rules = await api("/api/gpa/rules"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderGpa(view))); return; }
  // GPA за период: оценка (буква) или балл × кредиты (QM-02, п. 17)
  const letters = [["", "—"], ...rules.letter_scale.map((x) => [x.letter, `${x.letter} (${x.points})`]), ["P", "P (не в GPA)"]];
  const rows = el("div", { class: "stack" });
  const addRow = () => rows.append(el("div", { class: "row plan-quick" }, select(letters, "", { "aria-label": tr("ui.st.letter") }),
    input({ type: "number", min: 0, max: 4, step: "0.01", placeholder: tr("ui.st.points") }),
    input({ type: "number", min: 1, max: 30, value: 5, placeholder: tr("ui.st.credits") })));
  addRow(); addRow(); addRow();
  const gpaOut = el("div", {});
  const transfer = rules.transfer && rules.transfer.length ? el("section", { class: "card stack" }, numbered(2, tr("ui.st.transfer"),
    el("ul", { class: "do-list" }, rules.transfer.map((x) => el("li", {}, el("b", { text: x.to + ": " }), x.rule))), el("p", { class: "hint", text: rules.transfer_note }))) : null;
  view.replaceChildren(screenHead(EMOJI.gpa, tr("ui2.course.gpa"), tr("ui2.gpa.line")),
    el("section", { class: "card stack" }, numbered(1, tr("ui.st.gpa_calc"), el("p", { text: tr("ui.st.gpa_calc_sub") }),
      el("div", { class: "stack" }, rows, el("div", { class: "actions" },
        el("button", { class: "btn btn--secondary btn--small", type: "button", text: "＋", onclick: addRow }),
        el("button", { class: "btn btn--primary btn--small", type: "button", text: tr("ui.st.count"), onclick: async () => {
          const courses = [...rows.children].map((r) => ({ letter: r.children[0].value, points: Number(r.children[1].value || 0), credits: Number(r.children[2].value || 0) }))
            .filter((c) => r0(c) && (c.letter || c.points));
          try {
            const g = await api("/api/gpa/gpa", { method: "POST", body: { courses } });
            gpaOut.replaceChildren(el("div", { class: "answer-box" }, el("b", { text: `GPA: ${g.gpa}` }), ` · ${g.credits} ${tr("ui.st.credits")}`), el("p", { class: "hint", text: g.note }));
          } catch (e) { toast(e.message); }
        } })), gpaOut))),
    transfer,
    el("a", { class: "crosslink", href: "#honesty=grade", text: tr("ui2.grade.scale_link") }),
    el("p", { class: "hint" }, el("a", { href: rules.source_url, onclick: (e) => { e.preventDefault(); openLink(rules.source_url); }, text: `${tr("ui.source")}: ${rules.source_title}` }), ` · ${tr("ui.checked", { date: rules.checked })}`),
    el("p", { class: "disclaimer", text: tr("ui.st.gpa_privacy") }));
}
const r0 = (c) => c.credits > 0;

// ------------------------------------------------------------------ Помощник обращений

export async function renderAppeals(view) {
  view.replaceChildren(screenHead(EMOJI.appeals, tr("ui2.nav.appeals"), tr("ui.st.appeals_sub")), ...skeleton(2));
  let data;
  try { data = await api("/api/appeals"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderAppeals(view))); return; }
  let current = data.types[0];
  const form = el("div", { class: "stack" });
  const out = el("div", { class: "stack" });
  const draw = () => {
    const inputs = {};
    form.replaceChildren(el("p", {}, el("b", { text: tr("ui.st.where") + " " }), current.where),
      el("p", {}, el("b", { text: tr("ui.st.when") + " " }), current.deadline),
      ...current.fields.map((f) => { inputs[f.id] = f.multiline ? textarea({ maxlength: 1500 }) : input({ maxlength: 300 }); return field(f.label, inputs[f.id]); }),
      el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.st.make_draft"), onclick: async () => {
        try {
          const r = await api("/api/appeals/draft", { method: "POST", body: { type: current.id, fields: Object.fromEntries(Object.entries(inputs).map(([k, n]) => [k, n.value])) } });
          out.replaceChildren(supportBox(r.crisis), el("section", { class: "card stack" }, el("h3", { text: tr("ui.st.draft") }), el("div", { class: "pre", text: r.text }),
            el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.copy"), onclick: () => copyText(r.text) }),
            el("ul", { class: "do-list" }, r.tips.map((x) => el("li", { text: x }))),
            el("a", { href: r.source_url, onclick: (e) => { e.preventDefault(); openLink(r.source_url); }, text: `${tr("ui.source")}: ${r.source_title}` }),
            el("p", { class: "hint", text: tr("ui.st.you_send") })));
        } catch (e) { toast(e.message); }
      } }),
      current.contacts.length ? el("p", { class: "hint", text: `${current.service_title}: ${current.contacts.map((c) => c.value).join(", ")}` }) : null);
  };
  const chips = el("div", { class: "chips" }, data.types.map((ty) => el("button", { class: "chip", type: "button", "aria-pressed": String(ty === current), text: `${ty.emoji} ${ty.title}`,
    onclick: (e) => { current = ty; chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false")); e.currentTarget.setAttribute("aria-pressed", "true"); out.replaceChildren(); draw(); } })));
  draw();
  view.replaceChildren(screenHead(EMOJI.appeals, tr("ui2.nav.appeals"), tr("ui.st.appeals_sub")), chips, el("section", { class: "card" }, form), out,
    sectionHead(tr("ui.appeal.check_title")), appealCheck(),
    el("p", { class: "disclaimer", text: tr("ui.st.appeals_rules", { date: data.checked }) }));
}

/** Проверить готовое заявление или письмо из деканата: файл (PDF, Word, фото) → что от тебя хотят, сроки, куда идти.
    Файл разбирается и не сохраняется (та же загрузка, что у силлабуса, с согласием на обработку). */
function appealCheck() {
  const picker = filePicker({ accept: ".pdf,.docx,image/jpeg,image/png,image/webp", label: tr("ui.file.pick_doc") });
  const out = el("div", { class: "stack" });
  const go = el("button", { class: "btn btn--primary btn--block", type: "button", text: tr("ui.appeal.check_go"), onclick: async () => {
    if (!picker.file) { toast(tr("ui.file.need")); return; }
    go.disabled = true;
    out.replaceChildren(el("p", { class: "hint", text: tr("ui.file.reading") }), el("div", { class: "skeleton skeleton--block" }));
    try {
      const check = await apiWithConsent("/api/check", { method: "POST", body: { mode: "chek", text: "", file_base64: await fileToBase64(picker.file) } });
      window.location.hash = `#check=${check.id}`;
    } catch (e) { out.replaceChildren(el("p", { class: "error-text", text: e.message })); }
    go.disabled = false;
  } });
  return el("section", { class: "card stack" }, el("p", { class: "hint", text: tr("ui.appeal.check_sub") }), picker, go, out);
}
