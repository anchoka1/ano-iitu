/* 💬 IITU Hub — общение и жизнь университета (раньше «Сообщество»).
   Сверху сегменты на одном экране: 📰 Новости · ❓ Вопросы · 📊 Опросы · 🎉 Афиша. Ниже — группы, темы, поиск команды, потеряшки.
   Вопросы студентов проходят модерацию: автор видит статус в «Моих обращениях», решение бот присылает сообщением. */
import { api, el, tr, skeleton, errorState, emptyState } from "../core.js";
import { screenHead, sectionHead, linkCard, segments, newsCard, EMOJI } from "../ui.js";
import { feature } from "../campus_ui.js";
import { postForm, postRow, pulseBlock } from "./community.js";

const TABS = [["news", "news", null], ["questions", "questions", "ask_senior"], ["polls", "polls", "pulse"], ["events", "events", "events"]];

async function newsPane(box) {
  const data = await api("/api/news?limit=6");
  box.replaceChildren(
    ...(data.items.length ? data.items.slice(0, 5).map((n, i) => newsCard(n, { compact: i > 0 })) : [emptyState(EMOJI.news, tr("ui.news.empty"))]),
    linkCard({ emoji: EMOJI.news, title: tr("ui2.hub.all_news"), sub: tr("ui2.hub.all_news_sub"), href: "#news" }));
}

async function questionsPane(box, reload) {
  const items = await api("/api/posts?kind=senior_q&order=new");
  box.replaceChildren(
    sectionHead(tr("ui2.hub.ask_title")),
    postForm("senior_q", { onDone: () => setTimeout(reload, 600) }),
    sectionHead(tr("ui2.hub.recent_q"), "#community=senior_q", tr("ui.see_all")),
    items.length ? el("div", { class: "rows" }, items.slice(0, 10).map(postRow)) : emptyState(EMOJI.questions, tr("ui2.hub.no_q")));
}

async function pollsPane(box, reload) {
  box.replaceChildren(...(await pulseBlock(reload)));
}

async function eventsPane(box) {
  const items = await api("/api/posts?kind=event");
  box.replaceChildren(
    items.length ? el("div", { class: "rows" }, items.slice(0, 10).map(postRow)) : emptyState(EMOJI.events, tr("ui.cm.empty_event") !== "ui.cm.empty_event" ? tr("ui.cm.empty_event") : tr("ui.cm.empty")),
    linkCard({ emoji: EMOJI.events, title: tr("ui2.hub.add_event"), sub: tr("ui2.hub.add_event_sub"), href: "#community=event" }));
}

export async function render(view, _param, ctx) {
  const tabs = TABS.filter(([, , flag]) => !flag || feature(flag));
  const current = tabs.some(([id]) => id === ctx.cache.hubTab) ? ctx.cache.hubTab : "news";
  const pane = el("div", { class: "stack" }, ...skeleton(3));
  const reload = () => render(view, _param, ctx);
  const show = async (id) => {
    ctx.cache.hubTab = id;
    pane.replaceChildren(...skeleton(3));
    try {
      if (id === "news") await newsPane(pane);
      else if (id === "questions") await questionsPane(pane, reload);
      else if (id === "polls") await pollsPane(pane, () => show("polls"));
      else await eventsPane(pane);
    } catch (e) { pane.replaceChildren(errorState(e.message, () => show(id))); }
  };
  const more = [
    { emoji: EMOJI.groups, title: tr("ui.grp.title"), sub: tr("ui2.hub.groups_sub"), href: "#groups" },
    feature("hubs") ? { emoji: EMOJI.topics, title: tr("ui2.topics.title"), sub: tr("ui2.hub.topics_sub"), href: "#topics" } : null,
    feature("team_search") ? { emoji: EMOJI.team, title: tr("ui.cm.k_team"), sub: tr("ui.social.team_sub"), href: "#community=team" } : null,
    feature("lost_found") ? { emoji: EMOJI.lost, title: tr("ui.cm.k_lost"), sub: tr("ui.social.lost_sub"), href: "#community=lost" } : null,
  ].filter(Boolean);
  view.replaceChildren(screenHead(EMOJI.hub, "IITU Hub", tr("ui2.hub.line")),
    segments(tabs.map(([id, emoji]) => [id, `${EMOJI[emoji]} ${tr(`ui2.hub.t_${id}`)}`]), current, show), pane,
    sectionHead(tr("ui2.hub.more")), el("div", { class: "menu-list" }, more.map(linkCard)),
    el("p", { class: "disclaimer", text: tr("ui2.hub.moderation") }));
  show(current);
}
