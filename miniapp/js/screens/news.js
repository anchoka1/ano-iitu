/* Новости МУИТ: сайт iitu.edu.kz и канал @iitu_channel. Коротко своими словами, полный текст — по ссылке.
   Если источник недоступен — показываем сохранённое с пометкой «обновлено тогда-то». */
import { api, el, tr, state, toast, skeleton, errorState, openLink } from "../core.js";
import { newsCard, screenTitle, screenHead, EMOJI } from "../ui.js";

export async function render(view) {
  let source = "";
  const listBox = el("div", { class: "list" });
  const info = el("div", {});
  const chips = el("div", { class: "chips", role: "group" });
  for (const [key, label] of [["", tr("ui.news.all")], ["site", tr("ui.news.site")], ["telegram", tr("ui.news.telegram")]]) {
    chips.append(el("button", { class: "chip", type: "button", "aria-pressed": String(key === source), onclick: (e) => {
      source = key;
      chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
      e.currentTarget.setAttribute("aria-pressed", "true");
      load();
    } }, label));
  }
  const me = state.me || {};
  const subscribe = el("label", { class: "toggle" }, el("span", { text: tr("ui.news.subscribe") }),
    el("input", { type: "checkbox", checked: me.news_subscribed ? true : undefined, onchange: async (e) => {
      try {
        await api("/api/me/settings", { method: "PATCH", body: { news_subscribed: e.target.checked } });
        me.news_subscribed = e.target.checked;
      } catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    } }));

  view.replaceChildren(screenHead(EMOJI.news, tr("ui.news.title"), tr("ui.news.sub")), chips, info, listBox,
    el("section", { class: "card" }, subscribe));

  async function load() {
    listBox.replaceChildren(...skeleton(3, "skeleton--tall"));
    try {
      const data = await api(`/api/news?limit=20${source ? "&source=" + source : ""}`);
      const updated = data.updated_at ? new Date(data.updated_at).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "—";
      info.replaceChildren(el("p", { class: "hint" },
        data.stale ? tr("ui.news.stale") + " " : "", tr("ui.updated", { when: updated }), " · ",
        ...data.sources.flatMap((s, i) => [i ? " · " : "", el("a", { href: s.url, text: s.label, onclick: (e) => { e.preventDefault(); openLink(s.url); } })])));
      listBox.replaceChildren(...(data.items.length ? data.items.map((n) => newsCard(n)) : [el("p", { class: "card hint", text: tr("ui.news.empty") })]));
    } catch (e) {
      listBox.replaceChildren(errorState(e.message, load));
    }
  }
  load();
}
