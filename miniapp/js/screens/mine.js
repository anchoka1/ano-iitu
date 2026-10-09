/* «Мои обращения» (#mine): всё, что ты отправлял на проверку, и что с этим стало.
   У каждого — статус («На проверке» / «Опубликовано» / «Отклонено: причина» / вердикт по слуху)
   и где это видят остальные. Решение модератора бот ещё и присылает сообщением. */
import { api, el, tr, skeleton, errorState, emptyState, formatDate } from "../core.js";
import { screenTitle, menuList, screenHead, EMOJI } from "../ui.js";

const TONE = { pending: "muted", approved: "ok", rejected: "alert", hidden: "alert" };

export async function render(view) {
  view.replaceChildren(screenHead(EMOJI.mine, tr("ui.mine.title"), tr("ui.mine.sub")), ...skeleton(3));
  let items;
  try { items = await api("/api/me/submissions"); } catch (e) { view.replaceChildren(errorState(e.message, () => render(view))); return; }
  if (!items.length) {
    view.replaceChildren(screenHead(EMOJI.mine, tr("ui.mine.title"), tr("ui.mine.sub")),
      emptyState(EMOJI.mine, tr("ui.mine.empty")),
      menuList([
        { emoji: EMOJI.radar, title: tr("ui.radar.report"), sub: tr("ui.mine.try_radar"), href: "#radar" },
        { emoji: EMOJI.questions, title: tr("ui2.hub.ask_title"), sub: tr("ui.mine.try_question"), href: "#hub" },
        { emoji: EMOJI.check, title: tr("ui2.check.cta"), sub: tr("ui.mine.try_rumor"), href: "#verify=pravda" },
      ]));
    return;
  }
  view.replaceChildren(screenHead(EMOJI.mine, tr("ui.mine.title"), tr("ui.mine.sub")),
    el("div", { class: "stack" }, items.map((x) => el("a", { class: "card submission", href: x.link },
      el("div", { class: "row between" }, el("span", { class: "hint", text: x.kind_label }),
        el("span", { class: `verdict-chip verdict-chip--${x.tone || TONE[x.status] || "muted"}`, text: x.status_label })),
      el("div", { class: "submission__title", text: x.title || "—" }),
      x.reason ? el("p", { class: "submission__reason", text: tr("ui.mine.reason", { reason: x.reason }) }) : null,
      x.who_sees ? el("p", { class: "hint", text: x.who_sees }) : null,
      el("p", { class: "hint", text: x.decided_at ? tr("ui.mine.decided", { when: formatDate(x.decided_at) }) : tr("ui.mine.sent", { when: formatDate(x.created_at) }) })))));
}
