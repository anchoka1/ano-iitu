/* «Куда идти с каким вопросом»: каталог подразделений МУИТ с контактами с официальных страниц.
   #services=psych — открыть сразу нужное подразделение. */
import { api, el, tr, skeleton, errorState, openLink } from "../core.js";
import { numbered, moreButton, screenTitle, screenHead, EMOJI } from "../ui.js";

function contactNode(c) {
  if (c.type === "phone") return el("a", { href: "tel:" + c.value.replace(/[^\d+]/g, ""), text: c.value });
  if (c.type === "email") return el("a", { href: "mailto:" + c.value, text: c.value });
  if (c.type === "link") return el("a", { href: c.value, text: c.note || c.value, onclick: (e) => { e.preventDefault(); openLink(c.value); } });
  return document.createTextNode(c.value);
}

export async function render(view, param) {
  view.replaceChildren(screenHead(EMOJI.services, tr("ui.services.title"), tr("ui.services.sub")), ...skeleton(4));
  let data;
  try { data = await api("/api/services"); } catch (e) { view.replaceChildren(errorState(e.message, () => render(view, param))); return; }

  const search = el("input", { class: "input", type: "search", placeholder: tr("ui.services.search"), "aria-label": tr("ui.services.search") });
  const box = el("section", { class: "card" });
  const paint = () => {
    const q = search.value.trim().toLowerCase();
    const items = data.services.filter((s) => !q || `${s.title} ${s.for} ${s.tags || ""}`.toLowerCase().includes(q));
    box.replaceChildren(...items.map((s, i) => {
      const block = numbered(i + 1, `${s.emoji} ${s.title}`, el("p", { text: s.for }),
        // Нет контактов на официальной странице — честно пишем «скоро появятся» (добавляются в data/iitu/services.json).
        s.contacts.length ? el("ul", { class: "contacts" }, s.contacts.map((c) => el("li", {}, contactNode(c),
          c.note && c.type !== "link" ? el("span", { class: "hint", text: ` · ${c.note}` }) : null))) : el("span", { class: "soon", text: tr("ui2.contacts_soon") }),
        el("div", { class: "row" }, moreButton(s.source, tr("ui.source"))));
      block.id = "svc-" + s.id;
      return block;
    }), ...(items.length ? [] : [el("p", { class: "hint", text: "—" })]));
  };
  search.addEventListener("input", paint);
  paint();
  view.replaceChildren(screenHead(EMOJI.services, tr("ui.services.title"), tr("ui.services.sub")), search, box,
    el("p", { class: "hint", text: tr("ui.checked", { date: data.checked }) }));
  if (param) {
    const target = document.getElementById("svc-" + param);
    if (target) { target.scrollIntoView({ block: "start" }); target.style.outline = "2px solid var(--accent)"; }
  }
}
