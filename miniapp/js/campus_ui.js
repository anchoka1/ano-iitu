/*
  Общие помощники новых экранов (планер, хабы, учёба, сообщество, преподавателю).
  Используют те же классы, что и остальной Mini App (card, item, chips, btn) — новый визуальный язык не вводим.
*/
import { api, el, tr, toast, haptic, ask, state, openLink } from "./core.js";
import { formatDay } from "./ui.js";

/** Включена ли функция (флаги FEATURE_* с сервера). Пока флаги не загружены — считаем выключенной. */
export function feature(key) {
  return Boolean(state.features && state.features.flags && state.features.flags[key]);
}

export const isTeacher = () => Boolean(state.me && state.me.role === "teacher");
export const isVerifiedTeacher = () => Boolean(state.features && state.features.teacher_verified);

/** Поле формы с подписью. */
export function field(label, input) {
  return el("label", { class: "field" }, el("span", { text: label }), input);
}

export function input(attrs = {}) {
  return el("input", { class: "input", ...attrs });
}

export function textarea(attrs = {}) {
  return el("textarea", { class: "textarea", rows: 3, ...attrs });
}

export function select(options, value, attrs = {}) {
  return el("select", { class: "select", ...attrs },
    options.map(([v, label]) => el("option", { value: v, selected: String(v) === String(value) ? true : undefined }, label)));
}

/** Переключатель-чипы: items [[id, подпись]], onChange(id). */
export function chipSwitch(items, current, onChange) {
  const box = el("div", { class: "chips", role: "tablist" });
  items.forEach(([id, label]) => box.append(el("button", {
    class: "chip", type: "button", role: "tab", "aria-pressed": String(id === current),
    onclick: () => { box.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false")); box.querySelector(`[data-id="${id}"]`).setAttribute("aria-pressed", "true"); onChange(id); },
    "data-id": id,
  }, label)));
  return box;
}

/** «12 октября 2026, 18:00». */
export function dueLabel(t) {
  if (!t.due_date) return tr("ui.pl.no_date");
  return formatDay(t.due_date) + (t.due_time ? `, ${t.due_time}` : "");
}

/** Кнопка «Пожаловаться» — у любого пользовательского контента. */
export function reportButton(postId) {
  return el("button", { class: "btn btn--secondary btn--small", type: "button", text: tr("ui.cm.report"), onclick: async () => {
    if (!(await ask(tr("ui.cm.report_confirm")))) return;
    try { await api(`/api/posts/${postId}/report`, { method: "POST", body: { reason: "" } }); toast(tr("ui.cm.reported")); } catch (e) { toast(e.message); }
  } });
}

/** Кнопка «👍 полезно» с числом. */
export function voteButton(post, onDone) {
  return el("button", { class: post.voted ? "chip active" : "chip", type: "button", text: `${post.score || 0}`, onclick: async () => {
    try { const p = await api(`/api/posts/${post.id}/vote`, { method: "POST", body: { value: post.voted ? 0 : 1 } }); haptic("light"); onDone && onDone(p); } catch (e) { toast(e.message); }
  } });
}

/** Карточка поддержки (кризисные сигналы): контакты психологической службы и линий помощи. */
export function supportBox(card) {
  if (!card) return null;
  return el("div", { class: "callout callout--help stack" }, el("b", { text: card.title }),
    el("ul", { class: "do-list" }, (card.do || []).map((x) => el("li", { text: x }))),
    card.sources && card.sources[0] ? el("a", { href: card.sources[0].url, onclick: (e) => { e.preventDefault(); openLink(card.sources[0].url); } }, tr("ui.source")) : null);
}

/** Пометка «на модерации». */
export function pendingTag(post) {
  return post.status === "pending" ? el("span", { class: "tag", text: tr("ui.cm.pending") }) : null;
}

/** Скачать файл с сервера: в Telegram отправить в чат, в браузере — скачать. */
export async function getFile(path, sendPath, filename) {
  const { inTelegram, downloadBlob } = await import("./core.js");
  try {
    if (inTelegram && sendPath) { await api(sendPath, { method: "POST" }); toast(tr("ui.sent_to_chat")); return; }
    const res = await api(path, { raw: true });
    downloadBlob(await res.blob(), filename);
  } catch (e) { toast(e.message); }
}

/** Ссылка на бота с параметром или подсказка, если бот ещё не подключён. */
export function botDeepLink(param) {
  const username = state.me && state.me.bot_username;
  return username ? `https://t.me/${username}?start=${param}` : "";
}
