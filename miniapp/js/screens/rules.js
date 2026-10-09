/* Честные правила простым языком.
   #rights  (🧭 Навигатор) — ⚖️ права и обязанности студента по документам МУИТ + «разобрать мою ситуацию», спор, экстренная помощь.
   #honesty (📚 Предметы)  — 🧮 как считается оценка (QM-02, R-11) и 🤝 академическая честность (Кодекс K-02).
   #honesty=grade — сразу к оценке. Тексты — в ru.json (ui2.rights.*, ui2.honesty.*, adal.text), каждое правило со ссылкой на документ. */
import { api, el, tr, skeleton, errorState, openLink } from "../core.js";
import { screenHead, sectionHead, linkCard, safeHtml, moreButton, EMOJI } from "../ui.js";

const DOCS_PAGE = "https://iitu.edu.kz/ru/about-university/regulatory-legal-framework/";

// Частые ситуации → вопрос в «Вопрос о правилах МУИТ» (ответ только по официальным источникам).
const SITUATIONS = [
  ["ui.help.r_appeal", "Как подать апелляцию, если я не согласен с оценкой?"],
  ["ui.help.r_fx", "Что такое FX и как пересдать экзамен?"],
  ["ui.help.r_summer", "Как записаться на летний семестр и сколько кредитов можно взять?"],
  ["ui.help.r_leave", "Как оформить академический отпуск?"],
  ["ui.help.r_transfer", "Как перевестись или восстановиться?"],
  ["ui.help.r_trust", "Как сообщить о коррупции или нарушении в университете?"],
  ["ui.help.r_dorm", "Какие документы нужны для заселения в общежитие?"],
];

function contactList(service) {
  return el("ul", { class: "contacts" }, service.contacts.map((c) => {
    const value = c.type === "phone" ? el("a", { href: "tel:" + c.value.replace(/[^\d+]/g, ""), text: c.value })
      : c.type === "email" ? el("a", { href: "mailto:" + c.value, text: c.value })
        : c.type === "link" ? el("a", { href: c.value, text: c.note || c.value, onclick: (e) => { e.preventDefault(); openLink(c.value); } })
          : document.createTextNode(c.value);
    return el("li", {}, value, c.note && c.type !== "link" ? el("span", { class: "hint", text: ` · ${c.note}` }) : null);
  }));
}

// Формулировки прав и обязанностей — ui2.rights.r1–r6, d1–d6 в ru.json (по K-02, QM-02 и R-11, вычитаны владельцем проекта).
const items = (prefix, count) => Array.from({ length: count }, (_, i) => el("li", { text: tr(`${prefix}${i + 1}`) }));

export async function renderRights(view, _param, ctx) {
  const head = screenHead(EMOJI.rights, tr("ui2.rights.title"), tr("ui2.rights.line"));
  view.replaceChildren(head, ...skeleton(2));
  let services = [];
  try { services = (await api("/api/services")).services; } catch (e) { view.replaceChildren(head, errorState(e.message, () => renderRights(view, _param, ctx))); return; }
  const byId = Object.fromEntries(services.map((s) => [s.id, s]));
  const ask = (text) => { ctx.cache.prefill = text; ctx.nav("#verify=vopros"); };

  view.replaceChildren(head,
    linkCard({ emoji: EMOJI.rights, title: tr("ui2.check.prava"), sub: tr("ui2.rights.my_case_sub"), href: "#verify=prava", hero: true }),
    el("div", { class: "duo" },
      el("section", { class: "duo__col" }, el("h3", { text: `✅ ${tr("ui2.rights.rights")}` }), el("ul", {}, items("ui2.rights.r", 6))),
      el("section", { class: "duo__col duo__col--duty" }, el("h3", { text: `📌 ${tr("ui2.rights.duties")}` }), el("ul", {}, items("ui2.rights.d", 6)))),
    el("p", { class: "hint" }, tr("ui2.rights.sources"), " ", el("a", { href: DOCS_PAGE, onclick: (e) => { e.preventDefault(); openLink(DOCS_PAGE); }, text: tr("ui2.rights.docs_link") })),
    sectionHead(tr("ui2.rights.situations")),
    el("div", { class: "menu-list" }, SITUATIONS.map(([key, question]) => linkCard({ title: tr(key), sub: question, onclick: () => ask(question) }))),
    linkCard({ emoji: "🗣", title: tr("ui2.check.spor"), sub: tr("ui2.rights.spor_sub"), href: "#verify=spor" }),
    linkCard({ emoji: EMOJI.honesty, title: tr("ui2.honesty.title"), sub: tr("ui2.honesty.sub"), href: "#honesty" }),
    el("section", { class: "callout callout--help stack" }, el("h3", { text: `💙 ${tr("ui.help.crisis")}` }),
      ...["hotline", "psych"].filter((id) => byId[id]).map((id) => el("div", {}, el("b", { text: byId[id].title }), el("p", { class: "hint", text: byId[id].for }), contactList(byId[id])))),
    el("p", { class: "disclaimer", text: tr("ui.disclaimer") }));
}

export async function renderHonesty(view, param) {
  const head = screenHead(EMOJI.honesty, tr("ui2.honesty.title"), tr("ui2.honesty.line"));
  view.replaceChildren(head, ...skeleton(2));
  let rules;
  try { rules = await api("/api/gpa/rules"); } catch (e) { view.replaceChildren(head, errorState(e.message, () => renderHonesty(view, param))); return; }
  const scale = el("table", { class: "scale" },
    el("thead", {}, el("tr", {}, el("th", { text: tr("ui2.grade.t_letter") }), el("th", { text: "%" }), el("th", { text: tr("ui2.grade.t_points") }), el("th", { text: tr("ui2.grade.t_trad") }))),
    el("tbody", {}, rules.letter_scale.map((x) => el("tr", {}, el("td", { text: x.letter }), el("td", { text: `${x.min_percent}–${x.max_percent}` }),
      el("td", { text: String(x.points) }), el("td", { class: "hint", text: x.traditional })))));
  view.replaceChildren(head,
    el("div", { id: "grade" }, sectionHead(`${EMOJI.grade} ${tr("ui2.honesty.grade_title")}`, null, null, 1)),
    el("section", { class: "card stack" },
      el("div", { class: "formula" }, el("span", { class: "hint", text: tr("ui2.grade.formula_title") }), el("div", { class: "formula__eq", text: tr("ui2.grade.formula") }),
        el("div", { class: "hint", text: rules.admission_note })),
      el("ul", { class: "plain-list" }, el("li", { text: rules.pass_note }), el("li", { text: rules.gpa_note })),
      el("details", {}, el("summary", { text: tr("ui2.grade.scale") }), scale),
      el("p", { class: "hint" }, el("a", { href: rules.source_url, onclick: (e) => { e.preventDefault(); openLink(rules.source_url); }, text: `${tr("ui.source")}: ${rules.source_title}` }),
        ` · ${tr("ui.checked", { date: rules.checked })}`),
      el("a", { class: "crosslink", href: "#subjects", text: tr("ui2.honesty.to_subjects") })),
    sectionHead(`${EMOJI.honesty} ${tr("ui2.honesty.code_title")}`, null, null, 2),
    el("section", { class: "card prose" }, safeHtml(tr("adal.text"))),
    linkCard({ emoji: EMOJI.radar, title: tr("ui2.honesty.offer_title"), sub: tr("ui2.honesty.offer_sub"), href: "#verify=razvod" }),
    el("div", { class: "row" }, moreButton(DOCS_PAGE)));
  if (param === "grade") document.getElementById("grade")?.scrollIntoView();
}
