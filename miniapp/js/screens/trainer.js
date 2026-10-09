/* Тренажёр «Не дай себя обмануть»: выбор сценария, учебный диалог, разбор. */
import { api, el, tr, toast, haptic, skeleton, errorState, state } from "../core.js";
import { screenHead, EMOJI } from "../ui.js";

export async function render(view, _params, ctx) {
  view.replaceChildren(screenHead(EMOJI.trainer, tr("ui.trainer.title"), tr("ui.trainer.subtitle")), ...skeleton(4));
  try {
    const { scenarios, history } = await api("/api/trainer/scenarios");
    const immunity = state.me && state.me.immunity;
    view.replaceChildren(
      el("section", { class: "card" },
        screenHead(EMOJI.trainer, tr("ui.trainer.title"), tr("ui.trainer.subtitle")), el("p", { class: "sub", text: tr("ui.trainer.subtitle") }),
        immunity != null ? el("div", { class: "immunity", text: tr("ui.trainer.immunity", { n: immunity }) }) : null),
      el("div", { class: "list" }, scenarios.map((s) => {
        const best = history.filter((h) => h.scenario === s.id && h.immunity != null).map((h) => h.immunity);
        return el("button", { class: "item", onclick: async () => {
          try {
            const session = await api("/api/trainer/start", { method: "POST", body: { scenario: s.id } });
            haptic("light");
            ctx.nav(`#trainer=${session.id}`);
          } catch (e) { toast(e.message); }
        } },
        el("span", { class: "item__icon", text: s.emoji }),
        el("div", { class: "item__body" }, el("div", { class: "item__title", text: s.title }),
          el("div", { class: "hint", text: s.description }),
          best.length ? el("div", { class: "hint", text: Math.max(...best) + "%" }) : null));
      })),
      el("p", { class: "disclaimer", text: tr("ui.trainer.edu_note") }));
  } catch (e) {
    view.replaceChildren(errorState(e.message, () => render(view, _params, ctx)));
  }
}

function bubbles(messages) {
  return messages.map((m) => el("div", { class: `bubble bubble--${m.role === "bot" ? "bot" : "user"}`, text: m.text }));
}

function reviewBlock(session) {
  const r = session.review;
  const section = (title, items) => (items && items.length ? [el("h3", { text: title }), el("ul", { class: "do-list" }, items.map((x) => el("li", { text: x })))] : []);
  return el("section", { class: "card" },
    el("div", { class: "immunity", text: tr("ui.trainer.immunity", { n: r.immunity }) }),
    el("p", { text: r.summary }),
    ...section(tr("ui.trainer.red_flags"), r.red_flags),
    ...section(tr("ui.trainer.good"), r.good_moves),
    ...section(tr("ui.trainer.mistakes"), r.mistakes),
    ...section(tr("ui.trainer.tips"), r.tips),
    el("a", { class: "btn btn--secondary btn--block", href: "#trainer", text: tr("ui.trainer.again") }));
}

export async function renderSession(view, id, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let session;
  try { session = await api(`/api/trainer/${id}`); } catch (e) { view.replaceChildren(errorState(e.message, () => ctx.nav("#trainer"))); return; }
  const chat = el("div", { class: "chat" });
  const input = el("input", { class: "input", placeholder: tr("ui.trainer.placeholder"), maxlength: 500, "aria-label": tr("ui.trainer.placeholder") });
  const sendBtn = el("button", { class: "btn btn--primary", text: tr("ui.trainer.send") });
  const finishBtn = el("button", { class: "btn btn--secondary btn--block", text: tr("ui.trainer.finish") });
  const form = el("div", { class: "stack" }, el("div", { class: "row" }, input, sendBtn), finishBtn);
  const reviewBox = el("div");

  function paint() {
    chat.replaceChildren(...bubbles(session.messages));
    form.hidden = session.finished;
    reviewBox.replaceChildren(session.finished && session.review ? reviewBlock(session) : "");
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    session.messages.push({ role: "user", text });
    chat.append(...bubbles([{ role: "user", text }]), el("div", { class: "hint", text: tr("ui.trainer.thinking") }));
    sendBtn.disabled = true;
    try {
      session = await api(`/api/trainer/${id}/message`, { method: "POST", body: { text } });
      if (session.finished) haptic("success");
    } catch (e) { toast(e.message); }
    sendBtn.disabled = false;
    paint();
  }
  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  finishBtn.addEventListener("click", async () => {
    finishBtn.disabled = true;
    try { session = await api(`/api/trainer/${id}/finish`, { method: "POST" }); haptic("success"); } catch (e) { toast(e.message); }
    finishBtn.disabled = false;
    paint();
  });

  view.replaceChildren(el("section", { class: "card stack" }, el("h2", { text: session.title }), el("p", { class: "hint", text: tr("ui.trainer.edu_note") }), chat, form), reviewBox);
  paint();
}
