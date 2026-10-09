/* 🧭 Навигатор — куда и как обратиться в МУИТ.
   Главное действие — 🔎 «Проверить информацию». Дальше: 🚨 Радар разводов, ⚖️ права и обязанности студента,
   📝 заявления и обращения, 🏢 куда обратиться (справки, оплата, подразделения), 📖 вопрос о правилах МУИТ. */
import { el, tr } from "../core.js";
import { screenHead, linkCard, sectionHead, EMOJI } from "../ui.js";
import { feature, isTeacher } from "../campus_ui.js";

export function render(view) {
  view.replaceChildren(screenHead(EMOJI.navigator, tr("ui2.tab.navigator"), tr("ui2.nav.line")),
    linkCard({ emoji: EMOJI.check, title: tr("ui2.check.cta"), sub: tr("ui2.nav.check_sub"), href: "#verify", hero: true }),
    el("div", { class: "menu-list" },
      feature("scam_radar") ? linkCard({ emoji: EMOJI.radar, title: tr("ui.radar.title"), sub: tr("ui2.nav.radar_sub"), href: "#radar" }) : null,
      linkCard({ emoji: EMOJI.rights, title: tr("ui2.rights.title"), sub: tr("ui2.nav.rights_sub"), href: "#rights" }),
      feature("appeal_helper") ? linkCard({ emoji: EMOJI.appeals, title: tr("ui2.nav.appeals"), sub: tr("ui2.nav.appeals_sub"), href: "#appeals" }) : null,
      linkCard({ emoji: EMOJI.services, title: tr("ui2.nav.services"), sub: tr("ui2.nav.services_sub"), href: "#services" }),
      linkCard({ emoji: EMOJI.rules, title: tr("ui2.nav.ask"), sub: tr("ui2.nav.ask_sub"), href: "#verify=vopros" }),
      isTeacher() ? linkCard({ emoji: EMOJI.announce, title: tr("mode.obyavlenie.title"), sub: tr("mode.obyavlenie.desc"), href: "#verify=obyavlenie" }) : null),
    sectionHead(tr("ui2.nav.help_title")),
    el("a", { class: "callout callout--help", href: "#services=psych" },
      el("b", { text: `💙 ${tr("ui2.nav.help")}` }), el("span", { class: "hint", text: tr("ui2.nav.help_sub") })),
    el("p", { class: "disclaimer", text: tr("ui.disclaimer") }));
}
