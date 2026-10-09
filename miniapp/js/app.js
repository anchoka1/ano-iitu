/*
  Точка входа Mini App ANO IITU: Telegram, загрузка данных, переключение экранов.

  Шесть учебных разделов внизу — и всё остальное внутри них, не глубже двух уровней:
    #home       🎒 Учёба     — всё на сегодня: дедлайны, задачи, напоминания, «Проверить информацию»
    #subjects   📚 Предметы  — мои предметы; #subject=KEY — всё о предмете; #syllabus — загрузить силлабус; #honesty
    #course     🎓 Курс      — что важно на моём курсе, чек-лист, даты; #gpa, #quiz
    #plan       📅 План      — план семестра; #task=ID, #agreements, #agr=КОД, #boards, #board=ID, #focus, #week
    #navigator  🧭 Навигатор — куда и как обратиться; #verify, #check=ID, #history, #facts, #radar, #trainer, #rights, #appeals, #services
    #hub        💬 IITU Hub  — новости, вопросы и ответы, опросы, афиша; #post=ID, #community=ВИД, #groups, #grp=ID, #topic=ID
  Профиль — кнопка 👤 в шапке: #profile, #mine, #mod, #subs, #chats, #sources, #about, #onboarding.
  Кнопка «Назад» одна для всех экранов второго уровня: в Telegram — системная, в браузере — строка «‹ Раздел» сверху.
*/
import { api, el, tr, tg, inTelegram, state, errorState, haptic } from "./core.js";
import * as today from "./screens/today.js";
import * as subjects from "./screens/subjects.js";
import * as course from "./screens/course.js";
import * as plan from "./screens/plan.js";
import * as navigator from "./screens/navigator.js";
import * as iituhub from "./screens/iituhub.js";
import * as verify from "./screens/verify.js";
import * as history from "./screens/history.js";
import * as agreements from "./screens/agreements.js";
import * as trainer from "./screens/trainer.js";
import * as profile from "./screens/profile.js";
import * as news from "./screens/news.js";
import * as services from "./screens/services.js";
import * as about from "./screens/about.js";
import * as onboarding from "./screens/onboarding.js";
import * as groups from "./screens/groups.js";
import * as hubs from "./screens/hubs.js";
import * as community from "./screens/community.js";
import * as study from "./screens/study.js";
import * as teach from "./screens/teach.js";
import * as syllabus from "./screens/syllabus.js";
import * as facts from "./screens/facts.js";
import * as mine from "./screens/mine.js";
import * as mod from "./screens/mod.js";
import * as rules from "./screens/rules.js";

let view = document.getElementById("view");

/** Каждому переходу — новый контейнер экрана. Если старый экран ещё догружается (или перерисовывается
    после отправки формы), он пишет в отсоединённый контейнер и не может затереть новый экран. */
function freshView() {
  const next = view.cloneNode(false);
  view.replaceWith(next);
  view = next;
  return next;
}
const TABS = ["home", "subjects", "course", "plan", "navigator", "hub"];
const TOP_LEVEL = new Set([...TABS, "profile"]);
// Экран второго уровня → его раздел (подсветка вкладки и куда ведёт «Назад»).
const PARENT = {
  subject: "subjects", syllabus: "subjects", honesty: "subjects", hubs: "subjects", teach: "subjects", poll: "subjects", slots: "subjects",
  gpa: "course", quiz: "course",
  task: "plan", agreements: "plan", agr: "plan", boards: "plan", board: "plan", focus: "plan", week: "plan",
  verify: "navigator", check: "navigator", history: "navigator", facts: "navigator", radar: "navigator", trainer: "navigator",
  rights: "navigator", appeals: "navigator", services: "navigator",
  news: "hub", community: "hub", post: "hub", pulse: "hub", groups: "hub", grp: "hub", join: "hub", topic: "hub", topics: "hub",
  mine: "profile", mod: "profile", subs: "profile", chats: "profile", sources: "profile", about: "profile", onboarding: "profile",
};
// Старые адреса (ссылки из бота и закладки) → куда они переехали. Скрытые разделы ведут на ближайший по смыслу.
const MOVED = {
  more: "#profile", study: "#home", social: "#hub", path: "#course", checklist: "#course", capsule: "#course", morning: "#home",
  daily: "#home", rumor: "#facts", badges: "#profile", help: "#rights", adal: "#honesty", teacher: "#teach", verdict: "#week",
  navigator_course: "#course", career: "#navigator", rating: "#hub", family: "#profile", fam: "#profile", matrix: "#plan",
};

let mainHandler = null;
let lastDepth = 0;

const ctx = {
  cache: {},
  nav(hash) { if (window.location.hash === hash) route(); else window.location.hash = hash; },
  replace(hash) { window.location.replace(hash); },
  /** Нативная кнопка Telegram внизу (text=null — скрыть). */
  setMainButton(text, handler) {
    if (!inTelegram) return;
    if (mainHandler) tg.MainButton.offClick(mainHandler);
    mainHandler = null;
    if (!text) { tg.MainButton.hide(); return; }
    mainHandler = handler;
    tg.MainButton.setText(text);
    tg.MainButton.onClick(handler);
    tg.MainButton.show();
  },
};

function parseHash() {
  const raw = window.location.hash.slice(1);
  if (!raw || raw.startsWith("tgWebApp")) return { name: "home", param: "" };
  const [name, param = ""] = raw.split("=", 2);
  return { name, param: decodeURIComponent(param) };
}

/** Строка «‹ Раздел» над экранами второго уровня (вне Telegram; в Telegram — системная кнопка «Назад»). */
function backBar(parent) {
  if (inTelegram) return null;
  return el("a", { class: "back-link", href: "#" + parent, onclick: (e) => { e.preventDefault(); goBack(parent); } }, tr("ui2.back"));
}

function goBack(parent) {
  if (window.history.length > 1) window.history.back();
  else window.location.hash = "#" + (parent || "home");
}

async function route() {
  const { name, param } = parseHash();
  if (MOVED[name]) { window.location.replace(MOVED[name]); return; }
  ctx.setMainButton(null);
  window.scrollTo(0, 0);

  const top = TOP_LEVEL.has(name) && !(name === "plan" && param && param !== "week" && param !== "semester" && param !== "board");
  const parent = TOP_LEVEL.has(name) ? name : (PARENT[name] || "home");
  const tab = parent === "profile" ? "" : parent;
  document.querySelectorAll("#tabbar a").forEach((a) => {
    const active = a.dataset.tab === tab;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (inTelegram) { if (top) tg.BackButton.hide(); else tg.BackButton.show(); }

  // Плавный переход: вглубь — справа, назад — слева.
  const depth = top ? 0 : 1;
  const current = freshView();
  view.classList.remove("is-entering", "is-entering--back");
  view.classList.add("is-entering");
  if (depth < lastDepth) view.classList.add("is-entering--back");
  lastDepth = depth;

  const screens = {
    // 🎒 Учёба
    home: () => today.render(view, param, ctx),
    // 📚 Предметы
    subjects: () => subjects.render(view, param, ctx),
    subject: () => subjects.renderOne(view, param, ctx),
    syllabus: () => syllabus.render(view, param, ctx),
    sy: () => subjects.openSyllabus(param, ctx),
    honesty: () => rules.renderHonesty(view, param, ctx),
    hubs: () => hubs.render(view, param, ctx),
    hub: () => hubs.open(view, param, ctx),
    teach: () => teach.render(view, param, ctx),
    poll: () => teach.renderPoll(view, param),
    slots: () => teach.renderSlots(view, param),
    // 🎓 Курс
    course: () => course.render(view, param, ctx),
    gpa: () => study.renderGpa(view),
    quiz: () => course.renderQuiz(view, param, ctx),
    // 📅 План
    plan: () => plan.render(view, param, ctx),
    task: () => plan.renderTask(view, param, ctx),
    boards: () => plan.renderBoards(view, param, ctx),
    board: () => plan.renderBoardOne(view, param, ctx),
    focus: () => plan.renderFocus(view, param, ctx),
    week: () => plan.renderVerdict(view),
    agreements: () => agreements.render(view, param, ctx),
    agr: () => agreements.renderOne(view, param),
    // 🧭 Навигатор
    navigator: () => navigator.render(view, param, ctx),
    verify: () => verify.render(view, param, ctx),
    check: () => history.renderCheck(view, param),
    history: () => history.render(view),
    facts: () => facts.render(view, param, ctx),
    radar: () => facts.renderRadar(view, param, ctx),
    trainer: () => (param ? trainer.renderSession(view, param, ctx) : trainer.render(view, param, ctx)),
    rights: () => rules.renderRights(view, param, ctx),
    appeals: () => study.renderAppeals(view),
    services: () => services.render(view, param, ctx),
    // 💬 IITU Hub
    news: () => news.render(view, param, ctx),
    community: () => community.renderList(view, param, ctx),
    post: () => community.renderPost(view, param, ctx),
    pulse: () => community.renderPulse(view),
    groups: () => groups.render(view, param, ctx),
    grp: () => groups.renderOne(view, param, ctx),
    join: () => groups.renderJoin(view, param, ctx),
    topic: () => hubs.renderOne(view, param, ctx),
    topics: () => hubs.renderTopics(view, param, ctx),
    // 👤 Профиль
    profile: () => profile.render(view, param, ctx),
    mine: () => mine.render(view, param, ctx),
    mod: () => mod.render(view, param, ctx),
    subs: () => profile.renderSubscriptions(view),
    chats: () => profile.renderChats(view),
    sources: () => profile.renderSources(view),
    about: () => about.render(view),
    onboarding: () => onboarding.render(view, param, ctx),
  };
  // #hub без параметра — раздел IITU Hub; #hub=ID — старая ссылка на страницу хаба (бот, закладки).
  const run = name === "hub" && !param ? () => iituhub.render(view, param, ctx) : screens[name] || screens.home;
  try {
    await run();
    if (!top) {
      const bar = backBar(parent);
      if (bar && current.firstChild && !current.querySelector(":scope > .back-link")) current.prepend(bar);
    }
  } catch (e) {
    current.replaceChildren(errorState(e.message || tr("ui.error.generic"), route));
  }
}

function setupTelegram() {
  const forced = new URLSearchParams(window.location.search).get("theme");
  if (!inTelegram && (forced === "dark" || forced === "light")) document.documentElement.dataset.theme = forced;
  if (!tg || !inTelegram) return;
  tg.ready();
  tg.expand();
  const applyTheme = () => {
    const dark = tg.colorScheme === "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try {
      tg.setHeaderColor(dark ? "#121212" : "#F5F5F5");
      tg.setBackgroundColor(dark ? "#121212" : "#F5F5F5");
    } catch { /* старые клиенты Telegram не умеют */ }
  };
  applyTheme();
  tg.onEvent("themeChanged", applyTheme);
  tg.BackButton.onClick(() => {
    const { name } = parseHash();
    if (window.history.length > 1) window.history.back();
    else window.location.hash = "#" + (PARENT[name] || "home");
  });
}

/** Куда открыть приложение: параметр ссылки t.me/бот/app?startapp=... или ?check= / ?subject= из кнопки бота. */
function initialRoute(me) {
  const start = me.start_param || "";
  const query = new URLSearchParams(window.location.search);
  const map = [["check_", "#check="], ["agr_", "#agr="], ["grp_", "#join="], ["hub_", "#hub="], ["poll_", "#poll="],
    ["post_", "#post="], ["ack_", "#post="], ["slot_", "#slots="], ["sy_", "#sy="], ["subject_", "#subject="]];
  for (const [prefix, hash] of map) if (start.startsWith(prefix)) return hash + start.slice(prefix.length);
  const plain = { news: "#news", plan: "#plan", radar: "#radar", facts: "#facts", mine: "#mine", mod: "#mod", path: "#course",
    syllabus: "#syllabus", subjects: "#subjects", course: "#course", hub: "#hub", navigator: "#navigator", verify: "#verify" };
  if (plain[start]) return plain[start];
  if (query.get("check")) return `#check=${query.get("check")}`;
  if (query.get("subject")) return `#subject=${query.get("subject")}`;
  if (!me.onboarded && !window.location.hash.slice(1)) return "#onboarding";
  return null;
}

/** Лёгкий отклик на нажатие (вибрация в Telegram) — для всех кнопок и карточек. */
function setupHaptics() {
  document.addEventListener("click", (e) => {
    if (e.target.closest(".btn, .link-card, .tile, .chip, .segments button, .tabbar a, .subject-card, .kind")) haptic("select");
  }, { passive: true });
}

async function boot() {
  setupTelegram();
  setupHaptics();
  view.replaceChildren(el("div", { class: "skeleton skeleton--hero" }), el("div", { class: "skeleton skeleton--block" }), el("div", { class: "skeleton skeleton--block" }));
  try {
    const [strings, modes, me, features] = await Promise.all([api("/api/i18n/ru"), api("/api/modes"), api("/api/me"),
      api("/api/features").catch(() => ({ flags: {} }))]);
    state.strings = strings;
    state.modes = modes;
    state.me = me;
    state.features = features; // флаги FEATURE_*: выключенная функция не показывается
  } catch (e) {
    view.replaceChildren(errorState(e.message, boot));
    document.getElementById("greeting").textContent = "";
    return;
  }

  updateGreeting();
  const banner = document.getElementById("dev-banner");
  banner.textContent = "⚠️ " + tr("ui.dev_banner");
  banner.hidden = !state.me.is_dev;
  const badge = document.getElementById("demo-badge");
  badge.textContent = tr("ui.demo_badge");
  badge.hidden = state.me.llm !== "demo";
  document.body.classList.toggle("large", Boolean(state.me.large_font));
  document.getElementById("profile-dot").hidden = !(state.me.is_moderator && state.me.mod_queue);

  window.addEventListener("hashchange", route);
  window.addEventListener("profilechange", updateGreeting);
  const first = initialRoute(state.me);
  if (first && window.location.hash !== first) window.location.hash = first;
  else route();
}

function updateGreeting() {
  const me = state.me || {};
  document.getElementById("greeting").textContent = me.profile_label || tr("ui.hello", { name: me.first_name || "" });
}

boot();
