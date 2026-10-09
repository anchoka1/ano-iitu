/* История проверок и экран одной проверки. */
import { api, el, tr, skeleton, emptyState, errorState, formatDate, state } from "../core.js";
import { screenHead, linkCard, EMOJI } from "../ui.js";
import { renderCard } from "../card.js";

const PAGE = 30;

export async function render(view) {
  view.replaceChildren(screenHead(EMOJI.history, tr("ui.history.title"), tr("ui2.history.line")), ...skeleton(4));
  const listBox = el("div", { class: "rows" });
  const more = el("button", { class: "btn btn--secondary btn--block", text: tr("ui.history.more"), hidden: true });
  let offset = 0;

  async function load() {
    const items = await api(`/api/checks?limit=${PAGE}&offset=${offset}`);
    offset += items.length;
    more.hidden = items.length < PAGE;
    for (const item of items) {
      const mode = state.modes.find((m) => m.key === item.mode);
      listBox.append(el("a", { class: "row-item", href: `#check=${item.id}` },
        el("span", { class: `dot dot--${item.status}`, "aria-label": tr(`card.status.${item.status}`) }),
        el("div", { class: "row-item__body" },
          el("div", { class: "row-item__title", text: item.title }),
          el("div", { class: "row-item__sub", text: `${mode ? mode.title : tr("ui.history.old_mode")} · ${formatDate(item.created_at)}${item.in_group ? " · " + tr("ui.history.in_group") : ""}` }),
          el("div", { class: "row-item__sub", text: item.preview }))));
    }
    return items.length;
  }

  try {
    const count = await load();
    if (!count) { view.replaceChildren(screenHead(EMOJI.history, tr("ui.history.title"), tr("ui2.history.line")), emptyState(EMOJI.history, tr("ui.history.empty"), el("a", { class: "btn btn--primary btn--small", href: "#verify", text: tr("ui.history.go_check") }))); return; }
    more.addEventListener("click", async () => { more.disabled = true; await load().catch(() => {}); more.disabled = false; });
    view.replaceChildren(screenHead(EMOJI.history, tr("ui.history.title"), tr("ui2.history.line")), listBox, more);
  } catch (e) {
    view.replaceChildren(errorState(e.message, () => render(view)));
  }
}

export async function renderCheck(view, id) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  try {
    const check = await api(`/api/checks/${id}`);
    view.replaceChildren(renderCard(check, { onChange: () => renderCheck(view, id) }),
      el("div", { class: "menu-list" },
        linkCard({ emoji: EMOJI.check, title: tr("ui2.check.again"), sub: tr("ui2.check.again_sub"), href: "#verify" }),
        linkCard({ emoji: EMOJI.history, title: tr("ui.verify.history"), sub: tr("ui.verify.history_sub"), href: "#history" })));
  } catch (e) {
    view.replaceChildren(errorState(e.message, () => renderCheck(view, id)));
  }
}
