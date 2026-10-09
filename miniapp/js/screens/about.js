/* «О боте»: что это, что умеет, откуда берёт информацию, чего не делает, кто модерирует, как сообщить об ошибке. */
import { api, el, tr, openLink } from "../core.js";
import { bigNumbers, screenTitle, sectionHead, screenHead, EMOJI } from "../ui.js";

export async function render(view) {
  const sections = [["ui.about.what_t", "ui.about.what"], ["ui.about.can_t", "ui.about.can"], ["ui.about.src_t", "ui.about.src"],
    ["ui.about.mod_t", "ui.about.mod"], ["ui.about.not_t", "ui.about.not"], ["ui.about.bug_t", "ui.about.bug"]];
  const numbers = el("div", {});
  const pages = el("ul", { class: "sources" });
  view.replaceChildren(
    el("section", { class: "card stack" }, el("img", { class: "about-logo", src: "img/iitu-logo-horizontal.svg", alt: "МУИТ / IITU" }),
      el("h1", { class: "screen-head__title", text: "ANO IITU" }), el("p", { text: tr("ui2.today.mission") })),
    el("section", { class: "card stack" }, sections.map(([t, b]) => el("div", {}, el("h3", { text: tr(t) }), el("p", { class: "hint", text: tr(b) })))),
    numbers,
    sectionHead(tr("ui.about.pages")), el("section", { class: "card" }, pages),
    el("p", { class: "disclaimer", text: tr("app.status_note") }));
  try {
    const data = await api("/api/about");
    numbers.replaceChildren(bigNumbers([[data.official_sources, tr("ui.about.n_sources")], [data.modes, tr("ui.about.n_modes")],
      [data.services, tr("ui.about.n_services")]]));
    pages.replaceChildren(...data.source_pages.map((url) => el("li", {}, el("a", { href: url, text: url.replace("https://", ""),
      onclick: (e) => { e.preventDefault(); openLink(url); } }))), el("li", { class: "hint", text: tr("ui.checked", { date: data.checked }) }));
  } catch { /* цифры — не главное: экран работает и без них */ }
}
