/* «Проверенные слухи» (#facts) и 🚨 «Радар разводов» (#radar) — раздел «Навигатор». Общие ленты того, что проверил модератор.

   Радар: студент сообщает о схеме → модератор проверяет → запись в ленте у всех → подписчикам приходит
   предупреждение в боте → проверка информации учитывает её. Статус своей заявки — в «Профиль → Мои обращения».
   Проверенные слухи: слухи, которые не удалось подтвердить по источникам и кто-то отправил модератору, — с результатом проверки. */
import { api, el, tr, toast, haptic, skeleton, errorState, emptyState, openLink, formatDate } from "../core.js";
import { sectionHead, screenHead, linkCard, EMOJI } from "../ui.js";
import { feature } from "../campus_ui.js";
import { postForm } from "./community.js";

const TONE = { confirmed: "ok", refuted: "alert", partly: "warn", unconfirmed: "muted" };

export async function render(view) {
  view.replaceChildren(screenHead(EMOJI.check, tr("ui.facts.title"), tr("ui.facts.sub")), ...skeleton(3));
  let items;
  try { items = await api("/api/facts"); } catch (e) { view.replaceChildren(errorState(e.message, () => render(view))); return; }
  const list = items.length ? el("div", { class: "stack" }, items.map((f) => el("article", { class: "card fact" },
    el("span", { class: `verdict-chip verdict-chip--${TONE[f.verdict] || "muted"}`, text: f.label }),
    el("p", { class: "fact__claim", text: `«${f.claim}»` }),
    f.comment ? el("p", { text: f.comment }) : null,
    el("div", { class: "hint" }, [f.decided_on ? tr("ui.facts.checked_on", { date: f.decided_on }) : "", tr("ui.facts.times", { n: f.times_checked })].filter(Boolean).join(" · "),
      f.source_url ? el("span", {}, " · ", el("a", { href: f.source_url, text: tr("ui.source"), onclick: (e) => { e.preventDefault(); openLink(f.source_url); } })) : null))))
    : emptyState(EMOJI.check, tr("ui.facts.empty"), el("a", { class: "btn btn--primary btn--small", href: "#verify=pravda", text: tr("ui.facts.check_own") }));
  view.replaceChildren(screenHead(EMOJI.check, tr("ui.facts.title"), tr("ui.facts.sub")), list,
    el("p", { class: "hint", text: tr("ui.facts.how") }));
}

export async function renderRadar(view, _param, ctx) {
  view.replaceChildren(screenHead(EMOJI.radar, tr("ui.radar.title"), tr("ui.radar.sub")), ...skeleton(3));
  let data;
  try { data = await api("/api/radar"); } catch (e) { view.replaceChildren(errorState(e.message, () => renderRadar(view, _param, ctx))); return; }
  const reload = () => renderRadar(view, _param, ctx);
  const sub = el("input", { type: "checkbox", checked: data.subscribed ? true : undefined, onchange: async (e) => {
    try { await api("/api/subscriptions", { method: "PATCH", body: { key: "sub.radar", on: e.target.checked } }); haptic("light"); toast(e.target.checked ? tr("ui.radar.subscribed") : tr("ui.saved")); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  } });
  const mine = data.items.filter((p) => p.mine && p.status === "pending");
  const published = data.items.filter((p) => p.status === "approved");
  view.replaceChildren(...[screenHead(EMOJI.radar, tr("ui.radar.title"), tr("ui.radar.sub")),
    el("label", { class: "toggle card" }, el("span", {}, el("b", { text: tr("ui.radar.sub_toggle") }), el("br"), el("span", { class: "hint", text: tr("ui.radar.sub_hint") })), sub),
    mine.length ? el("a", { class: "callout", href: "#mine" }, el("b", { text: `${EMOJI.mine} ${tr("ui.radar.my_pending", { n: mine.length })}` }), el("span", { class: "hint", text: tr("ui.radar.my_status") })) : null,
    published.length ? el("div", { class: "rows" }, published.map((p) => el("a", { class: "row-item row-item--alert", href: `#post=${p.id}` },
      el("div", { class: "row-item__body" }, el("div", { class: "row-item__title", text: p.title }),
        el("div", { class: "row-item__sub", text: [p.body.slice(0, 140), formatDate(p.created_at)].filter(Boolean).join(" · ") })),
      el("span", { class: "arrow", text: "›", "aria-hidden": "true" }))))
      : emptyState(EMOJI.radar, tr("ui.radar.empty"), null, tr("ui2.radar.empty_title")),
    feature("scam_radar") ? sectionHead(tr("ui.radar.report")) : null,
    feature("scam_radar") ? el("p", { class: "hint", text: tr("ui.radar.report_hint") }) : null,
    feature("scam_radar") ? postForm("radar", { onDone: () => setTimeout(reload, 1200) }) : null,
    linkCard({ emoji: EMOJI.trainer, title: tr("ui.verify.trainer"), sub: tr("ui.verify.trainer_sub"), href: "#trainer" }),
    linkCard({ emoji: EMOJI.check, title: tr("ui2.check.cta"), sub: tr("ui2.radar.check_sub"), href: "#verify=razvod" })].filter(Boolean));
}
