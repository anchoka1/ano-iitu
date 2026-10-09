/* Договорённости: список, создание, карточка договорённости с подтверждением. */
import { api, el, tr, toast, haptic, skeleton, emptyState, errorState, formatDate, botLink, copyText, inTelegram, downloadBlob, state } from "../core.js";
import { screenHead, EMOJI } from "../ui.js";
import { feature } from "../campus_ui.js";
import { reportBlock } from "./teach.js";

const STATUS_ICON = { pending: "⏳", confirmed: "✅", declined: "❌", done: "🏁", cancelled: "🚫" };

export async function render(view, _params, ctx) {
  view.replaceChildren(screenHead(EMOJI.agreement, tr("ui.agr.title"), tr("ui2.agr.line")), ...skeleton(3));
  const textarea = el("textarea", { class: "textarea", rows: 3, placeholder: tr("ui.agr.placeholder"), "aria-label": tr("ui.agr.placeholder") });
  const groupBox = el("input", { type: "checkbox", checked: state.me && state.me.role === "teacher" ? true : undefined });
  const createBtn = el("button", { class: "btn btn--primary btn--block", text: tr("ui.agr.create"), onclick: async () => {
    if (textarea.value.trim().length < 3) return;
    createBtn.disabled = true;
    try {
      const agreement = await api("/api/agreements", { method: "POST", body: { text: textarea.value, group: groupBox.checked } });
      haptic("success");
      ctx.nav(`#agr=${agreement.code}`);
    } catch (e) { toast(e.message); }
    createBtn.disabled = false;
  } });
  const form = el("section", { class: "card stack" }, textarea,
    el("label", { class: "toggle" }, el("span", { text: tr("ui.agr.group") }), groupBox), createBtn);
  try {
    const items = await api("/api/agreements");
    const listBox = items.length ? el("div", { class: "list" }, items.map((a) => el("a", { class: "item", href: `#agr=${a.code}` },
      el("span", { class: "item__icon", text: STATUS_ICON[a.status] || "✍️" }),
      el("div", { class: "item__body" },
        el("div", { class: "item__title", text: a.draft.what || a.text }),
        el("div", { class: "item__sub", text: `${a.multi ? tr("ui.agr.group_short") + " · " : ""}${a.status_label} · ${formatDate(a.created_at)}${a.draft.deadline ? " · " + a.draft.deadline : ""}` })))))
      : emptyState("✍️", tr("ui.agr.empty"));
    view.replaceChildren(screenHead(EMOJI.agreement, tr("ui.agr.title"), tr("ui2.agr.line")), form, listBox);
  } catch (e) {
    view.replaceChildren(errorState(e.message, () => render(view, _params, ctx)));
  }
}

export async function renderOne(view, code) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let a;
  try { a = await api(`/api/agreements/${code}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderOne(view, code))); return; }
  const me = state.me.telegram_id;
  const d = a.draft || {};
  const rows = [["agr.field.who", d.who], ["agr.field.what", d.what || a.text], ["agr.field.amount", d.amount], ["agr.field.deadline", d.deadline],
    ["agr.field.breach", d.on_breach], ["agr.field.creator", a.creator_name],
    ["agr.field.counterparty", a.counterparty_name || (a.counterparty_username ? "@" + a.counterparty_username : "")]].filter(([, v]) => v);

  const act = async (action) => {
    try {
      await api(`/api/agreements/${code}/action`, { method: "POST", body: { action } });
      haptic("success");
      renderOne(view, code);
    } catch (e) { toast(e.message); }
  };
  const buttons = el("div", { class: "actions" });
  const people = a.participants || [];
  const answered = people.some((p) => p.user_id === me);
  const openForMe = a.multi ? (["pending", "confirmed"].includes(a.status) && a.creator_id !== me && !answered)
    : (a.status === "pending" && a.creator_id !== me);
  if (openForMe) {
    buttons.append(el("button", { class: "btn btn--primary", text: tr("ui.agr.confirm"), onclick: () => act("confirm") }),
      el("button", { class: "btn btn--secondary", text: tr("ui.agr.decline"), onclick: () => act("decline") }));
  }
  if (a.status === "pending" && a.creator_id === me) {
    buttons.append(el("button", { class: "btn btn--secondary", text: tr("ui.agr.cancel"), onclick: () => act("cancel") }));
  }
  const iConfirmed = people.some((p) => p.user_id === me && p.accepted);
  const member = a.creator_id === me || a.counterparty_id === me || iConfirmed;
  if (a.status === "confirmed" && member) buttons.append(el("button", { class: "btn btn--primary", text: tr("ui.agr.done"), onclick: () => act("done") }));
  if (member) {
    buttons.append(el("button", { class: "btn btn--secondary", text: tr("ui.agr.pdf"), onclick: async () => {
      try {
        if (inTelegram) { await api(`/api/agreements/${code}/send-pdf`, { method: "POST" }); toast(tr("ui.agr.pdf_sent")); }
        else downloadBlob(await (await api(`/api/agreements/${code}/pdf`, { raw: true })).blob(), `dogovorennost_${code}.pdf`);
      } catch (e) { toast(e.message); }
    } }));
  }

  const share = el("div", { class: "stack" });
  if ((a.status === "pending" || (a.multi && a.status === "confirmed")) && a.creator_id === me) {
    const link = botLink(`agr_${code}`);
    share.append(el("p", { class: "hint", text: link ? tr("ui.agr.share_hint") : tr("ui.agr.no_bot_link") }));
    if (link) share.append(el("div", { class: "pre", text: link }), el("button", { class: "btn btn--secondary btn--small", text: tr("ui.agr.copy_link"), onclick: () => copyText(link) }));
  }

  // Новое: отчёт «кто подтвердил, кто ещё нет» (автору) и командная доска из договорённости.
  const extra = el("div", { class: "stack" });
  if (a.creator_id === me && feature("agreement_report")) reportBlock(code).then((b) => b && extra.append(b));
  if (member && a.status === "confirmed" && feature("planner_team_board")) {
    extra.append(el("button", { class: "btn btn--secondary btn--block", text: tr("ui.pl.board_from_agr"), onclick: async () => {
      try { const b = await api("/api/boards", { method: "POST", body: { agreement_code: code } }); window.location.hash = "#board=" + b.id; } catch (e) { toast(e.message); }
    } }));
  }
  view.replaceChildren(extra, el("article", { class: "card" },
    el("span", { class: "status-pill", text: `${STATUS_ICON[a.status] || ""} ${a.status_label}` }),
    el("h2", { class: "verdict__title", text: tr(a.multi ? "agr.multi_title" : "agr.title") }),
    el("div", { class: "stack" }, rows.map(([k, v]) => el("div", {}, el("b", { text: tr(k) + " " }), v))),
    a.multi ? el("div", { class: "stack" },
      el("b", { text: tr("ui.agr.confirmed_by", { n: people.filter((p) => p.accepted).length }) }),
      el("div", { class: "chips" }, people.map((p) => el("span", { class: p.accepted ? "tag tag--accent" : "tag", text: p.name }))),
      el("p", { class: "hint", text: tr("agr.multi_hint") })) : null,
    d.missing && d.missing.length ? el("p", { class: "hint", text: `${tr("agr.missing")} ${d.missing.join("; ")}` }) : null,
    share, buttons,
    el("p", { class: "disclaimer", text: tr("agr.disclaimer") })));
}
