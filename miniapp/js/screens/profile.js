/* 👤 Профиль (кнопка в шапке): кто я (роль, курс — от этого зависят «Учёба», «Курс» и «Предметы»), 📬 «Мои обращения»,
   🛡 модерация (только модераторам), 🔔 уведомления, приватность: выгрузить и удалить свои данные.
   #profile, #subs (все подписки), #chats (мои группы с ботом), #sources (на что опирается проверка информации). */
import { api, el, tr, toast, skeleton, emptyState, errorState, inTelegram, downloadBlob, ask, openLink, state } from "../core.js";
import { sectionHead, menuList, screenHead, EMOJI } from "../ui.js";

/** Крупный шрифт — для удобного чтения (сохраняется в профиле). */
function largeFontToggle() {
  return el("label", { class: "toggle" }, el("span", { text: tr("ui.family.large_font") }),
    el("input", { type: "checkbox", checked: state.me.large_font ? true : undefined, onchange: async (e) => {
      const on = e.target.checked;
      document.body.classList.toggle("large", on);
      state.me.large_font = on;
      try { await api("/api/me/settings", { method: "PATCH", body: { large_font: on } }); } catch (err) { toast(err.message); }
    } }));
}

export async function render(view) {
  const me = state.me;
  const counts = el("div", {});
  const blocks = [
    screenHead(EMOJI.profile, me.first_name || tr("ui.profile.title"), tr("ui2.profile.line")),
    el("section", { class: "card row between" },
      el("div", {}, el("div", { class: "hint", text: tr("ui.profile.role") }), el("b", { text: me.profile_label || tr("ui.profile.no_role") }),
        me.staff_role ? el("div", { class: "hint", text: tr(`ui.mod.role_${me.staff_role}`) }) : null),
      el("a", { class: "btn btn--secondary btn--small", href: "#onboarding", text: me.onboarded ? tr("ui.profile.edit") : tr("ui.profile.fill") })),
    el("p", { class: "hint", text: tr("ui.profile.why_profile") }),
    counts,
  ];
  view.replaceChildren(...blocks, ...skeleton(1));
  let subs = { total: 0, pending: 0 };
  try {
    const items = await api("/api/me/submissions");
    subs = { total: items.length, pending: items.filter((x) => x.status === "pending").length };
  } catch { /* счётчик необязателен */ }
  counts.replaceChildren(menuList([
    { emoji: EMOJI.mine, title: tr("ui.mine.title"), sub: subs.total ? tr("ui.profile.mine_sub", { n: subs.total, p: subs.pending }) : tr("ui.profile.mine_empty"), href: "#mine",
      badge: subs.pending || null },
    me.is_moderator ? { emoji: EMOJI.mod, title: tr("ui.mod.title"), sub: tr("ui.profile.mod_sub"), href: "#mod", badge: me.mod_queue || null } : null,
  ]));

  const flag = (field, label) => el("label", { class: "toggle" }, el("span", { text: label }), el("input", { type: "checkbox", checked: me[field] ? true : undefined, onchange: async (e) => {
    try { await api("/api/me/settings", { method: "PATCH", body: { [field]: e.target.checked } }); me[field] = e.target.checked; }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  } }));

  const exportBtn = el("button", { class: "btn btn--secondary btn--block", type: "button", text: inTelegram ? tr("ui.profile.export_send") : tr("ui.profile.export"), onclick: async () => {
    try {
      if (inTelegram) { await api("/api/me/export/send", { method: "POST" }); toast(tr("ui.profile.export_sent")); }
      else downloadBlob(new Blob([JSON.stringify(await api("/api/me/export"), null, 2)], { type: "application/json" }), "ano_iitu_moi_dannye.json");
    } catch (e) { toast(e.message); }
  } });
  const deleteBtn = el("button", { class: "btn btn--danger btn--block", type: "button", text: tr("ui.profile.delete"), onclick: async () => {
    if (!(await ask(tr("ui.profile.delete_confirm")))) return;
    try { await api("/api/me", { method: "DELETE" }); toast(tr("ui.profile.deleted")); setTimeout(() => window.location.reload(), 1200); } catch (e) { toast(e.message); }
  } });

  view.replaceChildren(...blocks,
    sectionHead(`${EMOJI.reminders} ${tr("ui.profile.notifications")}`),
    el("section", { class: "card" }, flag("news_subscribed", tr("ui.profile.news")), flag("calendar_reminders", tr("ui.profile.calendar")),
      el("a", { class: "toggle", href: "#subs" }, el("span", { text: tr("ui.profile.more_subs") }), el("span", { class: "arrow", text: "›" }))),
    sectionHead(tr("ui.profile.more")),
    menuList([
      { emoji: EMOJI.groups, title: tr("ui.chats.title"), sub: tr("ui.profile.chats_sub"), href: "#chats" },
      { emoji: EMOJI.rules, title: tr("ui.sources.title"), sub: tr("ui.profile.sources_sub"), href: "#sources" },
      { emoji: EMOJI.about, title: tr("ui2.about.title"), sub: tr("ui.profile.about_sub"), href: "#about" },
    ]),
    el("section", { class: "card" }, largeFontToggle(),
      el("div", { class: "toggle" }, el("span", { text: tr("ui.profile.mode") }), el("b", { text: me.llm === "demo" ? tr("ui.profile.mode_demo") : tr("ui.profile.mode_real") }))),
    sectionHead(tr("ui.profile.privacy_title")),
    el("section", { class: "card stack" }, el("p", { text: tr("ui.profile.privacy") }), exportBtn, deleteBtn));
}

export async function renderSubscriptions(view) {
  view.replaceChildren(screenHead(EMOJI.reminders, tr("ui.subs.title"), tr("ui.subs.sub")), ...skeleton(2));
  let rows;
  try { rows = await api("/api/subscriptions"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderSubscriptions(view))); return; }
  view.replaceChildren(screenHead(EMOJI.reminders, tr("ui.subs.title"), tr("ui.subs.sub")),
    el("section", { class: "card" }, rows.map((sub) => el("label", { class: "toggle" }, el("span", { text: sub.title }),
      el("input", { type: "checkbox", checked: sub.on ? true : undefined, onchange: async (e) => {
        try { await api("/api/subscriptions", { method: "PATCH", body: { key: sub.key, on: e.target.checked } }); toast(tr("ui.saved")); }
        catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
      } })))),
    el("p", { class: "hint", text: tr("ui.subs.hint") }));
}

export async function renderChats(view) {
  view.replaceChildren(screenHead(EMOJI.groups, tr("ui.chats.title")), ...skeleton(2));
  try {
    const chats = await api("/api/chats");
    if (!chats.length) { view.replaceChildren(screenHead(EMOJI.groups, tr("ui.chats.title")), emptyState(EMOJI.groups, tr("ui.chats.empty"))); return; }
    view.replaceChildren(screenHead(EMOJI.groups, tr("ui.chats.title"), tr("ui.chats.week")), el("div", { class: "stack" }, chats.map((c) => el("section", { class: "card" },
      el("h3", { text: c.title || tr("ui.chats.group") }),
      el("div", { class: "stats" },
        el("div", { class: "stat" }, el("b", { text: String(c.week.total) }), el("small", { text: tr("ui.chats.total") })),
        el("div", { class: "stat" }, el("b", { text: String(c.week.red) }), el("small", { text: tr("ui.chats.red") })),
        el("div", { class: "stat" }, el("b", { text: String(c.week.votes) }), el("small", { text: tr("ui.chats.votes") })))))));
  } catch (e) { view.replaceChildren(errorState(e.message, () => renderChats(view))); }
}

export async function renderSources(view) {
  view.replaceChildren(screenHead(EMOJI.rules, tr("ui.sources.title")), ...skeleton(4));
  try {
    const data = await api("/api/sources");
    view.replaceChildren(screenHead(EMOJI.rules, tr("ui.sources.title"), tr("ui.sources.base_hint")),
      el("section", { class: "card" }, el("ul", { class: "sources" }, data.base.map((s) => el("li", {},
        s.url ? el("a", { href: s.url, text: s.title, onclick: (e) => { e.preventDefault(); openLink(s.url); } }) : s.title,
        s.is_demo ? el("span", { class: "demo-tag", text: tr("ui.card.demo_source") }) : null,
        el("div", { class: "hint", text: `${s.publisher} · ${tr("card.actual_on")} ${s.date}` }))))),
      el("section", { class: "card" }, el("h3", { text: tr("ui.sources.reputation") }), el("p", { class: "hint", text: tr("ui.sources.rep_hint") }),
        data.reputation.length ? el("ul", { class: "sources" }, data.reputation.map((r) => el("li", {}, el("b", { text: r.key }), " ",
          el("span", { class: "hint", text: tr("ui.sources.rep_row", r) })))) : el("p", { class: "hint", text: tr("ui.sources.rep_empty") })));
  } catch (e) { view.replaceChildren(errorState(e.message, () => renderSources(view))); }
}
