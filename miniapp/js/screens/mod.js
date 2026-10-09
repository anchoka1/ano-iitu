/* Модерация (#mod) — видна только модераторам, а права в любом случае проверяет сервер.
   #mod — очередь (всё, что ждёт проверки: Радар, вопросы, слухи, потеряшки, жалобы, заявки)
   #mod=log — журнал: кто, что и когда решил;  #mod=staff — модераторы (назначает админ).
   Те же заявки приходят в чат модераторов с кнопками — решать можно и там. */
import { api, el, tr, toast, haptic, skeleton, errorState, state, formatDate } from "../core.js";
import { screenTitle, screenHead, EMOJI } from "../ui.js";
import { field, input, textarea, select, chipSwitch } from "../campus_ui.js";

export async function render(view, param, ctx) {
  if (!state.me || !state.me.is_moderator) {
    view.replaceChildren(screenHead(EMOJI.mod, tr("ui.mod.title")), el("div", { class: "empty" }, el("p", { text: tr("ui.mod.only") })));
    return;
  }
  const tabs = chipSwitch([["queue", tr("ui.mod.queue")], ["log", tr("ui.mod.log")], ...(state.me.is_admin ? [["staff", tr("ui.mod.staff")]] : [])],
    param || "queue", (id) => ctx.nav(id === "queue" ? "#mod" : `#mod=${id}`));
  const body = el("div", { class: "stack" }, ...skeleton(3));
  view.replaceChildren(screenHead(EMOJI.mod, tr("ui.mod.title"), tr("ui.mod.sub")), tabs, body);
  try {
    if (param === "log") body.replaceChildren(await journal());
    else if (param === "staff") body.replaceChildren(await staff(() => render(view, param, ctx)));
    else body.replaceChildren(await queue(() => render(view, param, ctx)));
  } catch (e) { body.replaceChildren(errorState(e.message, () => render(view, param, ctx))); }
}

async function queue(reload) {
  const data = await api("/api/mod/queue");
  state.me.mod_queue = data.items.length;
  document.getElementById("profile-dot").hidden = !data.items.length;
  if (!data.items.length) return el("div", { class: "empty" }, el("p", { text: tr("ui.mod.empty") }));
  return el("div", { class: "stack" }, data.items.map((item) => itemCard(item, data, reload)));
}

function itemCard(item, data, reload) {
  const card = el("article", { class: "card stack mod-item" });
  const decide = async (body) => {
    try {
      const r = await api("/api/mod/decide", { method: "POST", body: { key: item.key, ...body } });
      haptic("success");
      toast(tr(`ui.mod.done_${r.status}`));
      card.replaceWith(el("p", { class: "hint", text: `${item.kind_label}: ${tr(`ui.mod.done_${r.status}`)}` }));
      if (state.me.mod_queue) state.me.mod_queue -= 1;
    } catch (e) { toast(e.message); if (e.status === 409) reload(); }
  };
  // DOM append(null) печатает текст «null» — поэтому собираем узлы в массив и отбрасываем пустые.
  card.append(...[
    el("div", { class: "row between" }, el("span", { class: "tag", text: item.kind_label }), el("span", { class: "hint", text: formatDate(item.created_at) })),
    item.type === "report" ? el("p", { class: "callout callout--error", text: tr("ui.mod.reports", { n: item.reports, reasons: (item.report_reasons || []).join("; ") || "—" }) }) : null,
    item.parent_title ? el("p", { class: "hint", text: tr("ui.mod.answer_to", { title: item.parent_title }) }) : null,
    item.title ? el("h3", { text: item.title }) : null,
    item.body ? el("p", { class: "pre", text: item.body }) : null,
    item.details && item.details.length ? el("dl", { class: "details" }, item.details.flatMap((x) => [el("dt", { text: x.label }), el("dd", { text: x.value })])) : null,
    item.hub ? el("p", { class: "hint", text: tr("ui.mod.hub", { hub: item.hub }) }) : null,
    item.hubs && item.hubs.length ? el("p", { class: "hint", text: tr("ui.mod.hub", { hub: item.hubs.join(", ") }) }) : null,
    item.type === "app" ? null : item.author_name ? el("p", { class: "hint", text: tr("ui.mod.author", { name: item.author_name }) }) : el("p", { class: "hint", text: tr("ui.mod.anon") }),
    item.claim ? claimInfo(item.claim) : null].filter(Boolean));

  const actions = el("div", { class: "actions" });
  const panel = el("div", { class: "stack" });
  if (item.type === "report") {
    actions.append(btn(tr("ui.mod.hide"), "danger", () => decide({ action: "hide", reason: "reports" })), btn(tr("ui.mod.keep"), "secondary", () => decide({ action: "keep" })));
  } else if (item.needs_verdict) {
    const verdict = select(data.verdicts.map((v) => [v.code, v.text]), "unconfirmed");
    const comment = textarea({ rows: 3, maxlength: 1000, placeholder: tr("ui.mod.comment_ph") });
    const url = input({ type: "url", maxlength: 500, placeholder: "https://" });
    panel.append(field(tr("ui.mod.verdict"), verdict), field(tr("ui.mod.comment"), comment), field(tr("ui.mod.source"), url),
      btn(tr("ui.mod.publish_verdict"), "primary", () => decide({ action: "approve", verdict: verdict.value, comment: comment.value, source_url: url.value.trim() })));
    actions.append(btn(tr("ui.mod.reject"), "secondary", () => showReject()));
  } else {
    actions.append(...[btn(tr("ui.mod.approve"), "primary", () => decide({ action: "approve" })),
      btn(tr("ui.mod.reject"), "secondary", () => showReject()),
      item.type === "post" ? btn(tr("ui.mod.edit"), "ghost", () => showEdit()) : null].filter(Boolean));
  }
  function showReject() {
    const reason = select([...data.reasons.map((r) => [r.code, r.text]), ["own", tr("ui.mod.own_reason")]], data.reasons[0].code);
    const own = textarea({ rows: 2, maxlength: 500, placeholder: tr("ui.mod.own_ph"), hidden: true });
    reason.addEventListener("change", () => { own.hidden = reason.value !== "own"; });
    panel.replaceChildren(field(tr("ui.mod.reason"), reason), own, el("p", { class: "hint", text: tr("ui.mod.reason_hint") }),
      btn(tr("ui.mod.reject_send"), "danger", () => decide({ action: "reject", reason: reason.value === "own" ? own.value : reason.value })));
  }
  function showEdit() {
    const title = input({ maxlength: 256, value: item.title });
    const body = textarea({ rows: 4, maxlength: 4000 });
    body.value = item.body || "";
    panel.replaceChildren(field(tr("ui.cm.f_title"), title), field(tr("ui.cm.f_body"), body),
      btn(tr("ui.mod.edit_send"), "primary", () => decide({ action: "edit", title: title.value, body: body.value })));
  }
  card.append(actions, panel);
  return card;
}

function claimInfo(c) {
  return el("div", { class: "callout" }, el("b", { text: tr("ui.mod.bot_said", { title: c.bot_title || "—" }) }),
    el("p", { class: "hint", text: tr("ui.facts.times", { n: c.times_checked }) }),
    c.sources.length ? el("ul", { class: "plain-list" }, c.sources.map((s) => el("li", { text: `${s.title}${s.url ? " — " + s.url : ""}` }))) : null);
}

function btn(text, kind, onclick) {
  return el("button", { class: `btn btn--${kind} btn--small`, type: "button", text, onclick: async (e) => {
    const button = e.currentTarget; // после await currentTarget уже пуст — запоминаем кнопку заранее
    button.disabled = true;
    try { await onclick(); } finally { button.disabled = false; }
  } });
}

async function journal() {
  const rows = await api("/api/mod/log");
  if (!rows.length) return el("div", { class: "empty" }, el("p", { text: tr("ui.mod.log_empty") }));
  return el("div", { class: "rows" }, rows.map((r) => el("div", { class: "row-item row-item--static" }, el("div", { class: "row-item__body" },
    el("div", { class: "row-item__title", text: `${r.moderator} ${r.action_label} · ${r.kind_label}` }),
    el("div", { class: "row-item__sub", text: [formatDate(r.created_at), r.target, r.reason].filter(Boolean).join(" · ") })))));
}

async function staff(reload) {
  const rows = await api("/api/mod/staff");
  const id = input({ type: "number", placeholder: "123456789" });
  const role = select([["moderator", tr("ui.mod.role_moderator")], ["admin", tr("ui.mod.role_admin")]], "moderator");
  return el("div", { class: "stack" },
    el("div", { class: "rows" }, rows.map((r) => el("div", { class: "row-item row-item--static" },
      el("div", { class: "row-item__body" }, el("div", { class: "row-item__title", text: r.name || String(r.user_id) }),
        el("div", { class: "row-item__sub", text: `${tr(`ui.mod.role_${r.role}`)} · id ${r.user_id}${r.source === "env" ? " · " + tr("ui.mod.from_env") : ""}` })),
      r.source === "app" ? el("button", { class: "btn btn--ghost btn--small", type: "button", text: tr("ui.mod.revoke"), onclick: async () => {
        try { await api(`/api/mod/staff/${r.user_id}`, { method: "DELETE" }); reload(); } catch (e) { toast(e.message); }
      } }) : null))),
    el("section", { class: "card stack" }, el("h3", { text: tr("ui.mod.grant") }), field(tr("ui.mod.grant_id"), id), field(tr("ui.mod.grant_role"), role),
      el("button", { class: "btn btn--primary", type: "button", text: tr("ui.mod.grant_send"), onclick: async () => {
        try { await api("/api/mod/staff", { method: "POST", body: { user_id: Number(id.value), role: role.value } }); haptic("success"); reload(); } catch (e) { toast(e.message); }
      } }), el("p", { class: "hint", text: tr("ui.mod.grant_hint") })));
}
