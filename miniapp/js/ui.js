/*
  UI-компоненты ANO IITU: заголовок экрана (смайлик + название + одна строка «что здесь»), заголовок блока «/01»,
  карточка-ссылка со смайликом и стрелкой, плитка раздела, кольцо прогресса, дата-плашка, сегменты, карточка новости.
  Все строят DOM через el() — чужой текст не исполняется как HTML.

  Смайлики — смысловые метки (один смайлик = одно значение во всём приложении), см. EMOJI.
*/
import { el, tr, openLink } from "./core.js";

/** Словарь смайликов: так один и тот же смайлик всегда значит одно и то же. */
export const EMOJI = {
  study: "🎒", subjects: "📚", course: "🎓", plan: "📅", navigator: "🧭", hub: "💬",
  check: "🔎", radar: "🚨", rights: "⚖️", appeals: "📝", services: "🏢", rules: "📖", announce: "📣",
  syllabus: "📄", grade: "🧮", deadline: "⏰", checklist: "✅", materials: "📁", questions: "❓", honesty: "🤝",
  agreement: "✍️", news: "📰", polls: "📊", events: "🎉", groups: "👥", team: "🛠", lost: "🧤", topics: "💡",
  profile: "👤", mine: "📬", mod: "🛡", week: "🏁", focus: "⏱", boards: "🗂", reminders: "🔔", trainer: "🎯",
  history: "🗃", dates: "🗓", gpa: "📈", quiz: "🧩", teacher: "🧑‍🏫", reviews: "⭐", slots: "🕑", about: "ℹ️",
};

/** Заголовок экрана: смайлик + название + одна строка простыми словами (что здесь и что нажать). */
export function screenHead(emoji, title, line) {
  return el("header", { class: "screen-head" },
    el("h1", { class: "screen-head__title" }, emoji ? el("span", { class: "screen-head__emoji", "aria-hidden": "true", text: emoji }) : null, title),
    line ? el("p", { class: "screen-head__line", text: line }) : null);
}

/** Старый вариант заголовка (без смайлика) — для экранов, которые его ещё используют. */
export function screenTitle(title, sub) {
  return [screenHead("", title, sub)];
}

/** Заголовок блока: «/01 ДЕДЛАЙНЫ ........ ВСЕ ›». n — номер блока (необязательно). */
export function sectionHead(title, href, linkText, n) {
  return el("div", { class: "section-head" }, el("h2", {}, n ? num(n) : null, title),
    href ? el("a", { href, text: (linkText || tr("ui.see_all")) + " ›" }) : null);
}

/** Номер блока в стиле сайта: /01, /02 ... */
export function num(n) {
  return el("span", { class: "num", text: "/" + String(n).padStart(2, "0") });
}

/** Нумерованный блок: /01 + заголовок + содержимое. */
export function numbered(n, title, ...content) {
  return el("div", { class: "numbered" }, num(n), el("div", {}, el("h3", { text: title }), ...content));
}

/** Карточка-ссылка: смайлик, заголовок, одна строка пояснения, стрелка. href — экран (#...), url — внешняя ссылка,
    onclick — действие. badge — число справа. hero — главная (малиновая) карточка экрана. */
export function linkCard({ emoji, title, sub, href, url, onclick, badge, hero }) {
  const body = el("div", { class: "link-card__body" }, el("div", { class: "link-card__title", text: title }),
    sub ? el("div", { class: "link-card__sub", text: sub }) : null);
  const lead = emoji ? el("span", { class: "link-card__emoji", "aria-hidden": "true", text: emoji }) : null;
  const tail = el("span", { class: "link-card__tail" }, badge ? el("span", { class: "count", text: String(badge) }) : null,
    el("span", { class: "arrow", text: "›", "aria-hidden": "true" }));
  const cls = hero ? "link-card link-card--hero" : "link-card";
  if (href) return el("a", { class: cls, href }, lead, body, tail);
  return el("button", { class: cls, type: "button", onclick: onclick || (() => openLink(url)) }, lead, body, tail);
}

/** Группа карточек-ссылок. items — параметры linkCard (null пропускается). */
export function menuList(items) {
  return el("div", { class: "menu-list" }, items.filter(Boolean).map((x) => linkCard(x)));
}

/** Плитка раздела (сетка 2×N): смайлик, название, строка пояснения. */
export function tile({ emoji, title, sub, href, onclick, accent }) {
  const children = [el("span", { class: "tile__emoji", "aria-hidden": "true", text: emoji }), el("span", { class: "tile__title", text: title }),
    sub ? el("span", { class: "tile__sub", text: sub }) : null];
  const cls = accent ? "tile tile--accent" : "tile";
  return href ? el("a", { class: cls, href }, ...children) : el("button", { class: cls, type: "button", onclick }, ...children);
}

export function tiles(items) {
  return el("div", { class: "tiles" }, items.filter(Boolean).map(tile));
}

/** Кольцо прогресса (SVG). percent 0–100; label — текст в центре (по умолчанию «NN%»); small — подпись под числом. */
export function ring(percent, { size = 64, stroke = 7, label, small, light = false, ok = false } = {}) {
  const ns = "http://www.w3.org/2000/svg";
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("aria-hidden", "true");
  const circle = (cls, offset) => {
    const node = document.createElementNS(ns, "circle");
    node.setAttribute("class", cls);
    node.setAttribute("cx", size / 2);
    node.setAttribute("cy", size / 2);
    node.setAttribute("r", r);
    node.setAttribute("fill", "none");
    node.setAttribute("stroke-width", stroke);
    if (offset !== undefined) {
      node.setAttribute("stroke-dasharray", c.toFixed(2));
      node.setAttribute("stroke-dashoffset", c.toFixed(2));
      // Кольцо «заполняется» после появления на экране — живая деталь, а не украшение.
      requestAnimationFrame(() => requestAnimationFrame(() => node.setAttribute("stroke-dashoffset", offset.toFixed(2))));
    }
    return node;
  };
  svg.append(circle("ring__track"), circle("ring__bar", c * (1 - p / 100)));
  const cls = ["ring", light ? "ring--light" : "", ok || p >= 100 ? "ring--ok" : ""].filter(Boolean).join(" ");
  return el("div", { class: cls, style: `width:${size}px;height:${size}px;font-size:${Math.max(11, size / 4.4)}px`, role: "img",
    "aria-label": `${Math.round(p)}%` },
  svg, el("div", { class: "ring__label" }, el("div", {}, el("b", { text: label ?? `${Math.round(p)}%` }), small ? el("small", { text: small }) : null)));
}

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const MON_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/** «2026-10-06» → «6 октября 2026». */
export function formatDay(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** «2026-10-06» → «6 октября» (без года) — для ближайших дат. */
export function shortDay(iso) {
  if (!iso) return "";
  const [, m, d] = iso.split("-").map(Number);
  return m && d ? `${d} ${MONTHS[m - 1]}` : iso;
}

/** Сколько дней до даты: «сегодня», «завтра», «через 3 дн.», «просрочено». */
export function daysLeft(iso) {
  if (!iso) return "";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(iso + "T00:00:00");
  const n = Math.round((day - today) / 86400000);
  if (n < 0) return tr("ui2.days.over");
  if (n === 0) return tr("ui2.days.today");
  if (n === 1) return tr("ui2.days.tomorrow");
  return tr("ui2.days.in", { n });
}

/** Дата-плашка «12 / ОКТ» у дедлайна. */
export function dateBadge(iso, overdue = false) {
  if (!iso) return el("div", { class: "date-badge date-badge--none" }, el("b", { text: "—" }), el("small", { text: tr("ui2.no_date_short") }));
  const [, m, d] = iso.split("-").map(Number);
  return el("div", { class: overdue ? "date-badge date-badge--over" : "date-badge" }, el("b", { text: String(d) }), el("small", { text: MON_SHORT[m - 1] }));
}

/** Сегменты (вкладки на одном экране, без перехода на новый уровень). items [[id, подпись]]. */
export function segments(items, current, onChange) {
  const box = el("div", { class: "segments", role: "tablist" });
  items.forEach(([id, label]) => box.append(el("button", { type: "button", role: "tab", "aria-pressed": String(id === current), "data-id": id,
    onclick: (e) => {
      box.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
      e.currentTarget.setAttribute("aria-pressed", "true");
      onChange(id);
    } }, label)));
  return box;
}

/** Кнопка «ПОДРОБНЕЕ» (внешняя ссылка). */
export function moreButton(url, text) {
  return el("a", { class: "more-btn", href: url, onclick: (e) => { e.preventDefault(); openLink(url); } }, text || tr("ui.more_link"), " →");
}

/** Блок крупных цифр. items: [[число, подпись], ...] */
export function bigNumbers(items) {
  return el("div", { class: "bignums" }, items.map(([value, label]) => el("div", { class: "bignum" }, el("b", { text: String(value) }), el("small", { text: label }))));
}

/** Карточка новости как в блоке «Новости и события» на сайте. */
export function newsCard(item, { compact = false } = {}) {
  const meta = el("div", { class: "news-card__meta" }, el("span", { text: formatDay(item.published) }),
    el("span", { class: item.source === "telegram" ? "tag" : "tag tag--accent", text: item.source_label }));
  const summary = item.summary ? el("p", { class: "news-card__summary" }, item.summary,
    item.summary_by !== "llm" ? el("span", { class: "hint", text: ` (${tr("ui.news.excerpt")})` }) : null) : null;
  const img = item.image ? el("img", { class: "news-card__img", src: item.image, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : null;
  // Компактная карточка без картинки — без пустой серой плашки.
  return el("a", { class: `news-card${compact && img ? " news-card--compact" : ""}`, href: item.url,
    onclick: (e) => { e.preventDefault(); openLink(item.url); } },
  img,
  el("div", { class: "news-card__body" }, meta, el("h3", { class: "news-card__title", text: item.title }),
    compact ? null : summary, compact ? null : el("span", { class: "more-btn", text: tr("ui.more_link") + " →" })));
}

/**
 * Текст с простой разметкой из наших же строк (ru.json): разрешены только <b>, <i> и ссылки https.
 * Всё остальное превращается в обычный текст — даже если строку кто-то испортит, код не выполнится.
 */
export function safeHtml(markup) {
  const doc = new DOMParser().parseFromString(`<div>${markup}</div>`, "text/html");
  const out = el("div", {});
  const walk = (node, target) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        child.textContent.split("\n").forEach((part, i) => { if (i) target.append(el("br")); target.append(part); });
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const tag = child.tagName.toLowerCase();
        let next = target;
        if (tag === "b" || tag === "i") { next = el(tag); target.append(next); }
        else if (tag === "a" && /^https:\/\//.test(child.getAttribute("href") || "")) {
          const url = child.getAttribute("href");
          next = el("a", { href: url, onclick: (e) => { e.preventDefault(); openLink(url); } });
          target.append(next);
        }
        walk(child, next);
      }
    }
  };
  walk(doc.body.firstChild, out);
  return out;
}

/** Строка события календаря: дата слева, название справа (без смайликов календаря — они пересекались бы со смайликами разделов). */
export function eventRow(ev) {
  return el("div", { class: "event" }, el("div", { class: "event__date", text: ev.dates }),
    el("div", {}, el("div", { class: "event__title", text: ev.title }),
      ev.note ? el("div", { class: "hint", text: ev.note }) : null));
}
