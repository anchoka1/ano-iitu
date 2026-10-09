/* 📄 Загрузить силлабус (#syllabus, раздел «Предметы»): PDF, Word или фото — или вставить текст.
   После разбора: предмет появляется в «Предметах», дедлайны с датами сами попадают в «План» (бот напомнит),
   веса оценок — в «Как считается оценка». Оригинал файла сервер не хранит: остаётся только разобранная карточка. */
import { api, el, tr, toast, haptic, apiWithConsent, filePicker, fileToBase64 } from "../core.js";
import { screenHead, EMOJI } from "../ui.js";
import { textarea } from "../campus_ui.js";
import { subjectKeyFor } from "./subjects.js";

export async function render(view, _param, ctx) {
  const linkHub = ctx.cache.syllabusHub || null; // загрузка со страницы дисциплины — сразу привяжем к ней
  ctx.cache.syllabusHub = null;
  const picker = filePicker({ accept: ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/jpeg,image/png,image/webp",
    label: tr("ui2.sy.pick") });
  const pasted = textarea({ rows: 6, maxlength: 20000, placeholder: tr("ui.sy.paste_ph") });
  const status = el("div", { class: "stack" });
  const go = el("button", { class: "btn btn--primary btn--block btn--big", type: "button", text: tr("ui2.sy.go"), onclick: submit });
  const pasteBox = el("details", {}, el("summary", { text: tr("ui.sy.paste") }), el("div", { class: "stack" }, pasted));

  async function submit() {
    if (!picker.file && pasted.value.trim().length < 20) { haptic("warning"); toast(tr("ui.sy.need")); return; }
    go.disabled = true;
    status.replaceChildren(el("p", { class: "hint", text: tr("ui2.sy.reading") }), el("div", { class: "skeleton skeleton--tall" }));
    try {
      let sy = picker.file
        ? await apiWithConsent("/api/syllabus/upload", { method: "POST", body: { file_base64: await fileToBase64(picker.file), file_name: picker.file.name } })
        : await api("/api/syllabus", { method: "POST", body: { text: pasted.value } });
      if (linkHub && sy.hub_id !== linkHub) sy = await api(`/api/syllabi/${sy.id}`, { method: "PATCH", body: { hub_id: linkHub } }).catch(() => sy);
      haptic("success");
      const added = sy.plan ? sy.plan.added : 0;
      toast(added ? tr("ui2.sy.done_plan", { n: added }) : tr("ui2.sy.done"));
      const list = await api("/api/subjects").catch(() => ({ items: [] }));
      ctx.replace(`#subject=${subjectKeyFor(sy.id, list.items) || "s" + sy.id}`);
    } catch (e) {
      haptic("error");
      status.replaceChildren(el("div", { class: "callout callout--error" }, el("b", { text: tr("ui.sy.failed") }), el("p", { text: e.message }),
        el("p", { class: "hint", text: tr("ui.sy.failed_hint") })));
      pasteBox.open = true;
    }
    go.disabled = false;
  }

  view.replaceChildren(screenHead(EMOJI.syllabus, tr("ui2.sy.title"), tr("ui2.sy.line")),
    el("section", { class: "card stack" },
      el("div", { class: "dropzone" }, el("span", { class: "dropzone__emoji", "aria-hidden": "true", text: EMOJI.syllabus }),
        el("b", { text: tr("ui2.sy.drop_title") }), el("span", { class: "hint", text: tr("ui2.sy.drop_sub") }), picker),
      pasteBox, go, status),
    el("section", { class: "card" },
      el("h3", { text: tr("ui2.sy.what_title") }),
      el("ol", { class: "plain-list" }, [1, 2, 3].map((i) => el("li", { text: tr(`ui2.sy.what_${i}`) })))),
    el("p", { class: "disclaimer", text: tr("ui.sy.privacy") }));
}
