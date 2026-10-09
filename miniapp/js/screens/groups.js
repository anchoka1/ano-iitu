/* «Группы и потоки» — университетская версия «Семьи».
   #groups — мои группы и создание; #grp=ID — карточка группы; #join=ТОКЕН — вступить по приглашению. */
import { api, el, tr, toast, haptic, ask, skeleton, emptyState, errorState, botLink, copyText, state, formatDate } from "../core.js";
import { screenTitle, sectionHead, linkCard, numbered, eventRow, screenHead, EMOJI } from "../ui.js";

export async function render(view, _param, ctx) {
  view.replaceChildren(screenHead(EMOJI.groups, tr("ui.grp.title"), tr("ui.grp.sub")), ...skeleton(2));
  let items, kinds, ref;
  try {
    [items, kinds, ref] = await Promise.all([api("/api/circles"), api("/api/circles/kinds"), api("/api/iitu/reference")]);
  } catch (e) { view.replaceChildren(errorState(e.message, () => render(view, _param, ctx))); return; }

  let kind = state.me && state.me.role === "teacher" ? "teacher" : "group";
  const kindChips = el("div", { class: "chips", role: "group" }, kinds.map((k) => el("button", {
    class: "chip", type: "button", "aria-pressed": String(k.id === kind),
    onclick: (e) => { kind = k.id; kindChips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false")); e.currentTarget.setAttribute("aria-pressed", "true"); },
  }, `${k.emoji} ${k.title}`)));
  const title = el("input", { class: "input", maxlength: 128, placeholder: tr("ui.grp.name_ph"), "aria-label": tr("ui.grp.name") });
  const myCourse = state.me && state.me.role === "student" ? state.me.course : "";
  const course = el("select", { class: "select", "aria-label": tr("ui.grp.course") },
    el("option", { value: "" }, tr("ui.grp.course_none")),
    ref.courses.map((c) => el("option", { value: c.id, selected: c.id === myCourse ? true : undefined }, c.title)));
  const createBtn = el("button", { class: "btn btn--primary btn--block", text: tr("ui.grp.create"), onclick: async () => {
    if (title.value.trim().length < 2) { toast(tr("grp.err.title")); return; }
    createBtn.disabled = true;
    try {
      const circle = await api("/api/circles", { method: "POST", body: { title: title.value, kind, course: course.value } });
      haptic("success");
      ctx.nav(`#grp=${circle.id}`);
    } catch (e) { toast(e.message); }
    createBtn.disabled = false;
  } });

  const list = items.length ? el("div", { class: "list" }, items.map((c) => linkCard({
    title: c.title, sub: `${c.kind_label} · ${tr("ui.grp.count", { n: c.members_count })} · ${tr("ui.grp.role." + c.my_role)}`, href: `#grp=${c.id}`,
  }))) : emptyState("👥", tr("ui.grp.empty"));

  view.replaceChildren(screenHead(EMOJI.groups, tr("ui.grp.title"), tr("ui.grp.sub")), list,
    sectionHead(tr("ui.grp.create")),
    el("section", { class: "card stack" },
      el("div", { class: "field" }, el("span", { text: tr("ui.grp.kind") }), kindChips),
      el("label", { class: "field" }, el("span", { text: tr("ui.grp.name") }), title),
      el("label", { class: "field" }, el("span", { text: tr("ui.grp.course") }), course),
      createBtn),
    el("p", { class: "hint", text: tr("ui.grp.rights_note") }));
}

export async function renderOne(view, id, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let g;
  try { g = await api(`/api/circles/${id}`); } catch (e) { view.replaceChildren(errorState(e.message, () => renderOne(view, id, ctx))); return; }
  const me = state.me.telegram_id;
  const admin = g.my_role === "owner" || g.my_role === "admin";
  const reload = () => renderOne(view, id, ctx);

  const toggle = (field, label) => el("label", { class: "toggle" }, el("span", { text: label }),
    el("input", { type: "checkbox", checked: g[field] ? true : undefined, onchange: async (e) => {
      try { await api(`/api/circles/${id}/settings`, { method: "PATCH", body: { [field]: e.target.checked } }); }
      catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    } }));

  const link = botLink(`grp_${g.invite_token}`);
  const invite = el("section", { class: "card stack" }, el("h3", { text: tr("ui.grp.invite") }),
    el("p", { class: "hint", text: tr("ui.grp.invite_hint") }),
    link ? el("div", { class: "pre", text: link }) : el("p", { class: "hint", text: tr("ui.agr.no_bot_link") }),
    link ? el("button", { class: "btn btn--secondary btn--small", text: tr("ui.agr.copy_link"), onclick: () => copyText(link) }) : null);

  const members = el("section", { class: "card" }, el("h3", { text: `${tr("ui.grp.members")} · ${g.members_count}` }),
    el("div", { class: "list" }, g.members.map((m) => {
      const actions = el("div", { class: "row" });
      if (g.my_role === "owner" && m.role !== "owner") {
        actions.append(el("button", { class: "btn btn--secondary btn--small", text: m.role === "admin" ? tr("ui.grp.make_member") : tr("ui.grp.make_admin"),
          onclick: async () => { try { await api(`/api/circles/${id}/members/${m.user_id}`, { method: "POST", body: { role: m.role === "admin" ? "member" : "admin" } }); reload(); } catch (e) { toast(e.message); } } }));
      }
      if (admin && m.role !== "owner" && m.user_id !== me && !(m.role === "admin" && g.my_role !== "owner")) {
        actions.append(el("button", { class: "btn btn--danger btn--small", text: tr("ui.grp.remove"),
          onclick: async () => { try { await api(`/api/circles/${id}/members/${m.user_id}`, { method: "DELETE" }); reload(); } catch (e) { toast(e.message); } } }));
      }
      return el("div", { class: "item" }, el("div", { class: "item__body" },
        el("div", { class: "item__title", text: `${m.name}${m.user_id === me ? " " + tr("ui.family.me") : ""}` }),
        el("div", { class: "item__sub", text: tr("ui.grp.role." + m.role) + (m.can_receive ? "" : " · " + tr("ui.grp.cant_receive")) })), actions);
    })));

  const blocks = [];
  if (admin) {
    const postText = el("textarea", { class: "textarea", rows: 4, maxlength: 1500, placeholder: tr("ui.grp.post_ph"), "aria-label": tr("ui.grp.post") });
    const result = el("div", { class: "stack" });
    const sendBtn = el("button", { class: "btn btn--primary", text: tr("ui.grp.send"), onclick: async () => {
      if (postText.value.trim().length < 3) return;
      sendBtn.disabled = true;
      try {
        const r = await api(`/api/circles/${id}/posts`, { method: "POST", body: { text: postText.value } });
        haptic("success"); toast(tr("ui.grp.sent", { sent: r.sent, total: r.recipients })); reload();
      } catch (e) { toast(e.message); }
      sendBtn.disabled = false;
    } });
    const checkBtn = el("button", { class: "btn btn--secondary", text: tr("ui.grp.check"), onclick: () => {
      ctx.cache.prefill = postText.value; ctx.nav("#verify=obyavlenie");
    } });
    const agrText = el("textarea", { class: "textarea", rows: 3, placeholder: tr("ui.teacher.agreement_ph"), "aria-label": tr("ui.grp.agreement") });
    const agrBtn = el("button", { class: "btn btn--primary btn--block", text: tr("ui.grp.agreement_send"), onclick: async () => {
      if (agrText.value.trim().length < 3) return;
      agrBtn.disabled = true;
      try {
        const a = await api(`/api/circles/${id}/agreements`, { method: "POST", body: { text: agrText.value } });
        haptic("success"); ctx.nav(`#agr=${a.code}`);
      } catch (e) { toast(e.message); }
      agrBtn.disabled = false;
    } });
    blocks.push(el("section", { class: "card" },
      numbered(1, tr("ui.grp.post"), el("div", { class: "stack" }, postText, el("div", { class: "actions" }, sendBtn, checkBtn), result)),
      numbered(2, tr("ui.grp.agreement"), el("div", { class: "stack" }, agrText, agrBtn))));
  }
  if (g.upcoming && g.upcoming.length) blocks.push(sectionHead(tr("ui.grp.upcoming")), el("section", { class: "card" }, g.upcoming.map(eventRow)));
  if (g.posts_list.length) {
    blocks.push(sectionHead(tr("ui.grp.history")), el("div", { class: "list" }, g.posts_list.map((p) => el("div", { class: "card" },
      el("div", { class: "hint", text: `${p.author} · ${formatDate(p.created_at)}` }), el("p", { class: "pre", text: p.text })))));
  }
  if (g.agreements.length) {
    blocks.push(sectionHead(tr("ui.grp.agreements")), el("div", { class: "list" }, g.agreements.map((a) => linkCard({ title: a.what, sub: tr("agr.status." + a.status), href: `#agr=${a.code}` }))));
  }

  const leaveBtn = el("button", { class: "btn btn--danger btn--block", text: tr("ui.grp.leave"), onclick: async () => {
    if (!(await ask(tr("ui.grp.leave_confirm")))) return;
    try { await api(`/api/circles/${id}/leave`, { method: "POST" }); ctx.nav("#groups"); } catch (e) { toast(e.message); }
  } });
  const deleteBtn = g.my_role === "owner" ? el("button", { class: "btn btn--danger btn--block", text: tr("ui.grp.delete"), onclick: async () => {
    if (!(await ask(tr("ui.grp.delete_confirm")))) return;
    try { await api(`/api/circles/${id}`, { method: "DELETE" }); ctx.nav("#groups"); } catch (e) { toast(e.message); }
  } }) : null;

  view.replaceChildren(
    el("h2", { text: g.title }), el("p", { class: "sub", text: `${g.kind_label} · ${tr("ui.grp.role." + g.my_role)}` }),
    ...blocks,
    el("section", { class: "card" }, el("h3", { text: tr("ui.grp.my_settings") }), toggle("alerts", tr("ui.grp.alerts")), toggle("posts", tr("ui.grp.posts"))),
    invite, members, leaveBtn, ...(deleteBtn ? [deleteBtn] : []),
    el("p", { class: "hint", text: tr("ui.grp.rights_note") }));
}

export async function renderJoin(view, token, ctx) {
  view.replaceChildren(el("div", { class: "skeleton skeleton--tall" }));
  let g;
  try { g = await api(`/api/circles/invite/${encodeURIComponent(token)}`); } catch (e) { view.replaceChildren(errorState(e.message)); return; }
  view.replaceChildren(el("section", { class: "card stack" },
    screenHead(EMOJI.groups, tr("ui.grp.join_title")),
    el("h3", { text: `${g.kind_label} · ${g.title}` }),
    el("p", { class: "hint", text: tr("ui.grp.count", { n: g.members_count }) }),
    el("p", { text: tr("ui.grp.sub") }),
    el("button", { class: "btn btn--primary btn--block", text: tr("ui.grp.join"), onclick: async () => {
      try {
        const joined = await api("/api/circles/join", { method: "POST", body: { token } });
        haptic("success"); toast(tr("ui.grp.joined")); ctx.nav(`#grp=${joined.id}`);
      } catch (e) { toast(e.message); }
    } })));
}
