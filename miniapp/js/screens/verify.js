/* 🔎 Проверка информации (раздел «Навигатор»).
   #verify        — выбрать, что проверяем: слух или новость · подозрительное сообщение · документ;
   #verify=MODE   — сразу нужный вид (prava, spor, vopros, obyavlenie, dogovor — со своих экранов).
   Вставляешь текст (и файл, если вид их принимает) → «Проверить» → экран «Результат проверки» (#check=ID).
   ctx.cache.prefill — подставить текст из другого экрана. */
import { api, el, tr, haptic, state, inTelegram, apiWithConsent, filePicker, fileToBase64 } from "../core.js";
import { screenHead, sectionHead, linkCard, EMOJI } from "../ui.js";
import { feature } from "../campus_ui.js";

// Три вида проверки на главном экране проверки: простые названия вместо названий режимов.
const KINDS = [
  { key: "pravda", emoji: EMOJI.check, title: "ui2.check.k_rumor", desc: "ui2.check.k_rumor_sub" },
  { key: "razvod", emoji: EMOJI.radar, title: "ui2.check.k_scam", desc: "ui2.check.k_scam_sub" },
  { key: "chek", emoji: "🧾", title: "ui2.check.k_doc", desc: "ui2.check.k_doc_sub" },
];
// Экраны, которые открывают проверку с одним видом (своё название и смайлик).
const SINGLE = {
  prava: { emoji: EMOJI.rights, title: "ui2.check.prava", line: "ui2.check.prava_line" },
  spor: { emoji: "🗣", title: "ui2.check.spor", line: "ui2.check.spor_line" },
  vopros: { emoji: EMOJI.rules, title: "ui2.nav.ask", line: "ui2.check.vopros_line" },
  obyavlenie: { emoji: EMOJI.announce, title: "mode.obyavlenie.title", line: "ui2.check.obyavlenie_line" },
  dogovor: { emoji: EMOJI.agreement, title: "ui2.check.dogovor", line: "ui2.check.dogovor_line" },
};

export async function render(view, param, ctx) {
  const single = SINGLE[param] ? param : null;
  let selected = state.modes.find((m) => m.key === (single || (KINDS.some((k) => k.key === param) ? param : "pravda")));
  const textarea = el("textarea", { class: "textarea", rows: 5, maxlength: 4000, "aria-label": tr("ui.verify.text_label") });
  const picker = filePicker({ accept: "image/jpeg,image/png,image/webp,.pdf,.docx", label: tr("ui.file.attach") });
  const checkBtn = el("button", { class: "btn btn--primary btn--block btn--big", type: "button", text: single === "vopros" ? tr("ui2.check.ask_go") : single === "dogovor" ? tr("ui.agr.create") : tr("ui2.check.go"), onclick: submit });
  const notice = el("p", { class: "error-text", hidden: true });
  const result = el("div", { class: "stack" });

  const kindList = el("div", { class: "kind-list", role: "radiogroup", "aria-label": tr("ui2.check.what") }, KINDS.map((k) =>
    el("button", { class: "kind", type: "button", role: "radio", "aria-checked": String(selected && selected.key === k.key), "data-key": k.key, onclick: () => choose(k.key) },
      el("span", { class: "kind__emoji", "aria-hidden": "true", text: k.emoji }),
      el("span", {}, el("span", { class: "kind__title", text: tr(k.title) }), el("span", { class: "kind__desc", text: tr(k.desc) })))));

  function choose(key) {
    selected = state.modes.find((m) => m.key === key);
    kindList.querySelectorAll(".kind").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.key === key)));
    paint();
    haptic("select");
  }
  function paint() {
    if (!selected) return;
    const ph = `ui.verify.ph_${selected.key}`;
    textarea.placeholder = tr(ph) !== ph ? tr(ph) : tr("ui.home.placeholder");
    picker.hidden = !selected.accepts_files;
    if (!selected.accepts_files) picker.reset();
    notice.hidden = true;
    result.replaceChildren();
    updateMain();
  }
  textarea.addEventListener("input", updateMain);
  picker.addEventListener("change", updateMain);

  function ready() { return selected && (textarea.value.trim() || picker.file); }
  function updateMain() {
    if (!inTelegram) return;
    checkBtn.hidden = true;
    ctx.setMainButton(ready() ? checkBtn.textContent : null, submit);
  }

  async function submit() {
    if (!selected) return;
    if (!textarea.value.trim() && !picker.file) { haptic("warning"); notice.hidden = false; notice.textContent = tr("ui.home.empty_text"); return; }
    notice.hidden = true;
    checkBtn.disabled = true;
    ctx.setMainButton(null);
    result.replaceChildren(el("p", { class: "hint", text: selected.key === "pravda" ? tr("ui.verify.searching") : tr("ui.home.checking") }), el("div", { class: "skeleton skeleton--tall" }));
    try {
      if (selected.key === "dogovor") {
        const agreement = await api("/api/agreements", { method: "POST", body: { text: textarea.value } });
        haptic("success");
        ctx.nav(`#agr=${agreement.code}`);
        return;
      }
      const body = { mode: selected.key, text: textarea.value };
      if (picker.file) body.file_base64 = await fileToBase64(picker.file);
      const check = await apiWithConsent("/api/check", { method: "POST", body });
      haptic(check.status === "red" ? "warning" : "success");
      ctx.nav(`#check=${check.id}`);
    } catch (e) {
      haptic("error");
      result.replaceChildren(el("p", { class: "error-text", text: e.message }));
    } finally {
      checkBtn.disabled = false;
      updateMain();
    }
  }

  const form = el("section", { class: "card stack" }, textarea, picker, checkBtn, notice, result);
  if (single) {
    const s = SINGLE[single];
    view.replaceChildren(screenHead(s.emoji, tr(s.title), tr(s.line)), form, el("p", { class: "disclaimer", text: tr("ui.disclaimer") }));
  } else {
    const factsBox = el("div", {});
    view.replaceChildren(screenHead(EMOJI.check, tr("ui2.check.title"), tr("ui2.check.line")),
      sectionHead(tr("ui2.check.what"), null, null, 1), kindList,
      sectionHead(tr("ui2.check.paste"), null, null, 2), form,
      el("section", { class: "callout" }, el("b", { text: `${EMOJI.honesty} ${tr("ui2.check.how_title")}` }), el("p", { class: "hint", text: tr("ui2.check.how") })),
      factsBox,
      el("div", { class: "menu-list" },
        linkCard({ emoji: EMOJI.history, title: tr("ui.verify.history"), sub: tr("ui.verify.history_sub"), href: "#history" }),
        linkCard({ emoji: EMOJI.rules, title: tr("ui.sources.title"), sub: tr("ui2.check.sources_sub"), href: "#sources" })),
      el("p", { class: "disclaimer", text: tr("ui.disclaimer") }));
    if (feature("fact_feed")) {
      api("/api/facts").then((items) => {
        if (!items.length) return;
        factsBox.replaceChildren(sectionHead(`${EMOJI.check} ${tr("ui.facts.title")}`, "#facts", tr("ui.see_all")),
          el("div", { class: "rows" }, items.slice(0, 3).map((f) => el("a", { class: `row-item row-item--${f.verdict === "confirmed" ? "ok" : f.verdict === "refuted" ? "alert" : "warn"}`, href: "#facts" },
            el("div", { class: "row-item__body" }, el("div", { class: "row-item__title", text: `«${f.claim}»` }), el("div", { class: "row-item__sub", text: f.label })),
            el("span", { class: "arrow", text: "›", "aria-hidden": "true" })))));
      }).catch(() => {});
    }
  }
  paint();
  if (ctx.cache.prefill) {
    textarea.value = ctx.cache.prefill;
    ctx.cache.prefill = "";
    updateMain();
  }
}
