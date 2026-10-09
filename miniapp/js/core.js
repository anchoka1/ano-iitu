/*
  Общие вещи для всех экранов: Telegram, запросы к API, тексты, мелкие помощники.

  export — делает функцию доступной другим файлам (они пишут import { api } from "./core.js").
*/

// Объект Telegram. Вне Telegram его может не быть, или initData пустая (открыто в браузере).
export const tg = window.Telegram ? window.Telegram.WebApp : null;
export const inTelegram = Boolean(tg && tg.initData);

// Общее состояние приложения (тексты, данные пользователя)
export const state = { strings: {}, me: null, modes: [], features: { flags: {} } };

/** Текст по ключу с подстановкой {name}. Нет ключа — показываем сам ключ (так ошибку видно). */
export function tr(key, params = {}) {
  const template = state.strings[key] || key;
  return template.replace(/\{(\w+)\}/g, (_, name) => (name in params ? params[name] : `{${name}}`));
}

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

/**
 * Запрос к нашему API. Каждый запрос несёт подпись Telegram: "Authorization: tma <initData>".
 * Сервер сам проверяет подпись; initDataUnsafe мы НЕ используем.
 */
export async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  if (inTelegram) headers.Authorization = "tma " + tg.initData;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch (e) {
    throw new ApiError(0, tr("ui.error.network"));
  }
  if (raw && response.ok) return response; // для картинок и файлов
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const fallback = response.status === 401 ? tr("ui.error.auth") : tr("ui.error.generic");
    throw new ApiError(response.status, data.error || fallback);
  }
  return data;
}

/**
 * Создать элемент. Текст ставим через textContent — он НЕ исполняет HTML,
 * поэтому чужой текст (сообщение мошенника) не сможет внедрить код (защита от XSS).
 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Лёгкая вибрация на телефоне в Telegram. */
export function haptic(kind) {
  if (!tg || !tg.HapticFeedback) return;
  if (kind === "select") tg.HapticFeedback.selectionChanged();
  else if (kind === "light") tg.HapticFeedback.impactOccurred("light");
  else tg.HapticFeedback.notificationOccurred(kind); // success | warning | error
}

let toastTimer;
export function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 3200);
}

/** Подтверждение: в Telegram — нативное окно, в браузере — стандартное. */
export function ask(message) {
  return new Promise((resolve) => {
    if (inTelegram && tg.showConfirm) tg.showConfirm(message, (ok) => resolve(Boolean(ok)));
    else resolve(window.confirm(message));
  });
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(tr("ui.copied"));
  } catch {
    window.prompt(tr("ui.copy"), text); // запасной вариант: показать текст для ручного копирования
  }
}

/** Открыть внешнюю ссылку: в Telegram — через openLink (в браузере телефона), иначе новая вкладка. */
export function openLink(url) {
  if (inTelegram && url.startsWith("https://t.me/")) tg.openTelegramLink(url);
  else if (inTelegram) tg.openLink(url);
  else window.open(url, "_blank", "noopener");
}

/** Скачать файл в браузере (в Telegram скачивание работает плохо — там шлём файл в чат). */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function skeleton(count = 3, cls = "skeleton--block") {
  return Array.from({ length: count }, () => el("div", { class: `skeleton ${cls}` }));
}

/** Пустое состояние: смайлик, что здесь будет и что нажать. icon — смайлик раздела (может быть пустым). */
export function emptyState(icon, text, action, title) {
  return el("div", { class: "empty" }, icon ? el("div", { class: "empty__icon", "aria-hidden": "true", text: icon }) : null,
    title ? el("p", { class: "empty__title", text: title }) : null, el("p", { text }), action || null);
}

/** Понятная ошибка с кнопкой «Повторить». */
export function errorState(message, retry) {
  return el("div", { class: "empty empty--error" }, el("div", { class: "empty__icon", "aria-hidden": "true", text: "⚠️" }),
    el("p", { class: "empty__title", text: tr("ui.error.title") }),
    el("p", { text: message }),
    retry ? el("button", { class: "btn btn--secondary btn--small", text: tr("ui.retry"), onclick: retry }) : null);
}

export function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}


/** Ссылка на бота с параметром (deep link). Нужен username бота с сервера. */
export function botLink(param) {
  const username = state.me && state.me.bot_username;
  return username ? `https://t.me/${username}?start=${param}` : "";
}

/** Прочитать файл как base64 (для отправки фото/PDF на сервер). */
export function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]); // убираем "data:...;base64,"
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/**
 * Запрос с загрузкой файла: если сервер ответил 428 (нет согласия на обработку документов),
 * показываем короткий текст согласия и, если человек согласен, повторяем запрос.
 */
export async function apiWithConsent(path, options) {
  try {
    return await api(path, options);
  } catch (e) {
    if (e.status !== 428) throw e;
    const info = await api("/api/me/consent");
    if (!(await sheet(tr("ui.consent.title"), info.text, tr("ui.consent.ok"), tr("ui.consent.no")))) throw new ApiError(428, tr("ui.consent.declined"));
    await api("/api/me/consent", { method: "POST", body: { given: true } });
    return api(path, options);
  }
}

/** Кнопка «Выбрать файл» с подписью выбранного файла и проверкой размера ещё до отправки. */
export function filePicker({ accept, maxMb = 10, label }) {
  const input = el("input", { type: "file", accept, hidden: true });
  const name = el("span", { class: "hint" });
  const clear = el("button", { class: "btn btn--ghost btn--small", type: "button", text: tr("ui.file.remove"), hidden: true });
  const pick = el("button", { class: "btn btn--secondary", type: "button", text: label || tr("ui.file.pick"), onclick: () => input.click() });
  const box = el("div", { class: "file-row" }, pick, name, clear, input);
  box.file = null;
  const reset = () => { box.file = null; input.value = ""; name.textContent = ""; clear.hidden = true; box.dispatchEvent(new Event("change")); };
  clear.addEventListener("click", reset);
  input.addEventListener("change", () => {
    const f = input.files[0];
    if (!f) return;
    if (f.size > maxMb * 1024 * 1024) { toast(tr("ui.file.too_big", { mb: maxMb })); reset(); return; }
    box.file = f;
    name.textContent = f.name;
    clear.hidden = false;
    box.dispatchEvent(new Event("change"));
  });
  box.reset = reset;
  return box;
}

/** Иконки вкладок и пунктов меню — простые линии, без эмодзи (SVG строим через DOM, без innerHTML). */
const ICONS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  study: "M4 5h7a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H4zM20 5h-4a3 3 0 0 0-2 .8M20 5v13h-6",
  check: "M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6zM8.5 12l2.5 2.5 4.5-5",
  people: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21v-1a6 6 0 0 1 12 0v1M16 3.5a4 4 0 0 1 0 7.5M22 21v-1a6 6 0 0 0-4-5.6",
  user: "M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM4 21v-1a7 7 0 0 1 14 0v1",
  chevron: "m9 6 6 6-6 6",
};

export function icon(name, cls = "icon") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name] || ICONS.chevron);
  svg.append(path);
  return svg;
}

/** Нижнее окно с текстом и двумя кнопками (для длинных текстов: у Telegram showConfirm лимит 256 символов). */
export function sheet(title, text, okLabel, cancelLabel) {
  return new Promise((resolve) => {
    const close = (value) => { overlay.remove(); resolve(value); };
    const overlay = el("div", { class: "sheet-overlay", onclick: (e) => { if (e.target === overlay) close(false); } },
      el("div", { class: "sheet", role: "dialog", "aria-modal": "true" },
        el("h3", { text: title }), el("p", { text }),
        el("div", { class: "sheet__actions" },
          el("button", { class: "btn btn--secondary", type: "button", text: cancelLabel, onclick: () => close(false) }),
          el("button", { class: "btn btn--primary", type: "button", text: okLabel, onclick: () => close(true) }))));
    document.body.append(overlay);
  });
}
