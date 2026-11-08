// Fund Facts — chat UI (no build step). Talks to the FastAPI backend on the same origin.
//
// Safety: all user and assistant text is inserted with textContent, never innerHTML
// (edge case IN-11). Icons are static SVG strings from icons.js.

import { api, ApiError } from "./api.js";
import { hydrateIcons, icon } from "./icons.js";
import { findPII } from "./pii.js";

const STORAGE_KEY = "fundfacts.threads.v1";
const MAX_CHARS_FALLBACK = 500;

// Reply kind -> label, icon and colour (never colour alone: label + icon always shown).
const KINDS = {
  answer: { label: "Fact", icon: "check", tone: "" },
  refusal: { label: "Can't advise", icon: "shield", tone: "tone-blue" },
  performance: { label: "Returns not shown", icon: "chart", tone: "tone-slate" },
  clarify: { label: "Need a detail", icon: "help", tone: "tone-violet" },
  not_found: { label: "Not found", icon: "search", tone: "tone-grey" },
  out_of_scope: { label: "Out of scope", icon: "ban", tone: "tone-grey" },
  greeting: { label: "Welcome", icon: "sparkle", tone: "" },
  pii_blocked: { label: "Personal info blocked", icon: "lock", tone: "tone-amber" },
  unavailable: { label: "Temporarily unavailable", icon: "alert", tone: "tone-coral" },
};

const state = {
  meta: null,
  threads: [],            // [{id, title, last_scheme, created_at, updated_at}]
  activeId: null,
  messages: new Map(),    // threadId -> [message]
  loaded: new Set(),
  pending: new Set(),     // threads waiting for a reply
  errors: new Map(),      // threadId -> {message, retryText}
  drafts: new Map(),      // threadId -> unsent text
  confirmDelete: null,    // threadId awaiting delete confirmation
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

// ------------------------------------------------------------------ storage (UI-06/THR-06)

function loadIds() {
  try {
    const ids = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(ids) ? ids.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveIds() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.threads.map((t) => t.id)));
  } catch {
    /* storage unavailable (private mode): threads last for this session only */
  }
}

// ------------------------------------------------------------------ formatting

function maxChars() {
  return (state.meta && state.meta.max_message_chars) || MAX_CHARS_FALLBACK;
}

function displayDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function clockTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function relativeTime(iso) {
  if (!iso) return "";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 172800) return "Yesterday";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

function shortUrl(url) {
  return url.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "");
}

function activeThread() {
  return state.threads.find((t) => t.id === state.activeId) || null;
}

function announce(text) {
  const region = $("live-region");
  region.textContent = "";
  window.setTimeout(() => { region.textContent = text; }, 50);
}

// ------------------------------------------------------------------ threads

async function createThread() {
  const thread = await api.createThread();
  state.threads.unshift(thread);
  state.messages.set(thread.id, []);
  state.loaded.add(thread.id);
  saveIds();
  return thread;
}

async function newChat() {
  closeDrawer();
  const current = activeThread();
  const msgs = current ? state.messages.get(current.id) || [] : [];
  if (current && !msgs.length && !state.pending.has(current.id)) {
    focusComposer();       // already on an empty chat
    return;
  }
  try {
    const thread = await createThread();
    await selectThread(thread.id);
  } catch (err) {
    showGlobalError(err);
  }
}

async function selectThread(id) {
  saveDraft();
  state.activeId = id;
  state.confirmDelete = null;
  closeDrawer();
  render();
  restoreDraft();
  if (!state.loaded.has(id)) {
    try {
      state.messages.set(id, await api.messages(id));
      state.loaded.add(id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        dropThread(id);     // deleted elsewhere (THR-07)
        return;
      }
      state.errors.set(id, { message: err.message });
    }
    if (state.activeId === id) render({ scroll: true });
  } else {
    render({ scroll: true });
  }
  focusComposer();
}

async function deleteThread(id) {
  state.confirmDelete = null;
  try {
    await api.deleteThread(id);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) {
      showGlobalError(err);
      return;
    }
  }
  await dropThread(id);
}

async function dropThread(id) {
  state.threads = state.threads.filter((t) => t.id !== id);
  ["messages", "errors", "drafts"].forEach((k) => state[k].delete(id));
  state.loaded.delete(id);
  state.pending.delete(id);
  saveIds();
  if (state.activeId === id) {
    if (state.threads.length) await selectThread(state.threads[0].id);   // UI-09
    else await newChat();
  } else {
    render();
  }
}

// ------------------------------------------------------------------ sending

async function send(rawText) {
  const id = state.activeId;
  const text = rawText.trim();
  if (!id || !text || state.pending.has(id)) return;
  if (text.length > maxChars()) {
    showComposerWarning(`Please keep questions under ${maxChars()} characters.`);
    return;
  }
  const pii = findPII(text);
  if (pii.length) {
    showComposerWarning(
      `This looks like personal information (${pii.join(", ")}). For your security, please remove it before sending.`,
    );
    return;
  }
  hideComposerWarning();

  const list = state.messages.get(id) || [];
  const optimistic = { id: null, role: "user", text, created_at: new Date().toISOString(), local: true };
  list.push(optimistic);
  state.messages.set(id, list);
  state.errors.delete(id);
  state.pending.add(id);
  state.drafts.delete(id);
  if (state.activeId === id) setComposerText("");
  render({ scroll: true });

  try {
    const result = await api.send(id, text);
    const msgs = state.messages.get(id) || [];
    const index = msgs.indexOf(optimistic);
    if (result.user_message) msgs.splice(index, 1, result.user_message);
    else msgs.splice(index, 1);            // blocked as PII by the server: never stored
    msgs.push(result.message);
    const thread = state.threads.find((t) => t.id === id);
    if (thread) Object.assign(thread, result.thread);
    state.threads.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    if (state.activeId === id) announce(`Reply: ${result.message.text}`);
  } catch (err) {
    const msgs = state.messages.get(id) || [];
    const index = msgs.indexOf(optimistic);
    if (index >= 0) msgs.splice(index, 1);
    if (err instanceof ApiError && (err.status === 404 || err.status === 410)) {
      state.pending.delete(id);
      await dropThread(id);
      return;
    }
    // Keep what the user typed (UI-04) and offer a retry.
    if (!state.drafts.get(id)) state.drafts.set(id, text);
    if (state.activeId === id) setComposerText(state.drafts.get(id));
    state.errors.set(id, { message: err.message, retryText: text });
    if (state.activeId === id) announce(err.message);
  } finally {
    state.pending.delete(id);
    render({ scroll: state.activeId === id });
  }
}

// ------------------------------------------------------------------ rendering

function render({ scroll = false } = {}) {
  renderSidebar();
  renderChat(scroll);
  renderComposer();
}

function renderSidebar() {
  const list = $("thread-list");
  list.replaceChildren();
  if (!state.threads.length) {
    list.append(el("li", "empty-threads", "No chats yet — start one with New chat."));
    return;
  }
  for (const thread of state.threads) {
    const item = el("li", "thread-item");
    const button = el("button", "thread-btn");
    button.type = "button";
    button.setAttribute("aria-current", String(thread.id === state.activeId));
    button.append(el("span", "thread-title", thread.title));
    const sub = el("span", "thread-sub");
    if (state.pending.has(thread.id)) {
      sub.append(el("span", "pending-dot"), el("span", null, "Answering…"));
    } else {
      sub.append(el("span", null, relativeTime(thread.updated_at)));
    }
    button.append(sub);
    button.addEventListener("click", () => selectThread(thread.id));

    const del = el("button", "thread-delete");
    del.type = "button";
    del.setAttribute("aria-label", `Delete chat: ${thread.title}`);
    del.append(icon("trash"));
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      state.confirmDelete = thread.id;
      renderSidebar();
      document.querySelector(".confirm .btn-danger")?.focus();
    });
    // Button + delete icon share a row, so the icon stays centred on the thread even when
    // the confirmation box opens underneath.
    const row = el("div", "thread-row");
    row.append(button, del);
    item.append(row);

    if (state.confirmDelete === thread.id) {
      const box = el("div", "confirm");
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", "Confirm delete");
      box.append(el("p", null, "Delete this chat? This can't be undone."));
      const actions = el("div", "confirm-actions");
      const yes = el("button", "btn btn-danger btn-sm", "Delete");
      yes.type = "button";
      yes.addEventListener("click", () => deleteThread(thread.id));
      const no = el("button", "btn btn-secondary btn-sm", "Cancel");
      no.type = "button";
      no.addEventListener("click", () => { state.confirmDelete = null; renderSidebar(); });
      actions.append(yes, no);
      box.append(actions);
      item.append(box);
    }
    list.append(item);
  }
}

function renderChat(scroll) {
  const column = $("message-column");
  const id = state.activeId;
  const thread = activeThread();
  $("chat-title").textContent = thread && thread.title !== "New chat" ? thread.title : "Fund Facts";

  const msgs = (id && state.messages.get(id)) || [];
  const loading = id && !state.loaded.has(id) && !state.errors.has(id);
  column.replaceChildren();

  if (loading) {
    column.append(thinkingCard("Loading chat…"));
  } else if (!msgs.length && !state.pending.has(id)) {
    column.append(welcomeView());
  }
  msgs.forEach((m, i) => column.append(m.role === "user" ? userMessage(m) : assistantCard(m, msgs, i)));
  if (state.pending.has(id)) column.append(thinkingCard("Thinking…"));
  const error = state.errors.get(id);
  if (error) column.append(errorCard(error));

  hydrateIcons(column);
  const box = $("messages");
  if (!msgs.length && !state.pending.has(id)) {
    stickToBottom = false;
    box.scrollTop = 0;                       // welcome view starts at the top
  } else if (scroll) {
    stickToBottom = true;
    box.scrollTop = box.scrollHeight;
  }
}

// Keep the newest message in view while the user is at the bottom, even when content
// grows after rendering (web font loading, a reply arriving). Scrolling up releases it.
let stickToBottom = false;
function watchScroll() {
  const box = $("messages");
  box.addEventListener("scroll", () => {
    stickToBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  }, { passive: true });
  new ResizeObserver(() => {
    if (stickToBottom) box.scrollTop = box.scrollHeight;
  }).observe($("message-column"));
}

function welcomeView() {
  const meta = state.meta || {};
  const wrap = el("section", "welcome");
  wrap.setAttribute("aria-label", "Welcome");
  const mark = el("div", "welcome-mark");
  mark.append(icon("doc"));
  wrap.append(mark, el("h2", null, "Fund Facts Assistant"));
  wrap.append(el("p", "welcome-text", meta.welcome ||
    "I answer factual questions about selected HDFC Mutual Fund schemes."));

  const tryBox = el("div", "try");
  tryBox.append(el("p", "try-label", "Try asking"));
  for (const question of meta.example_questions || []) {
    const b = el("button", "example");
    b.type = "button";
    b.append(el("span", "dot"), el("span", "example-text", question), icon("arrow"));
    b.addEventListener("click", () => send(question));
    tryBox.append(b);
  }
  wrap.append(tryBox);

  if (meta.schemes && meta.schemes.length) {
    const schemes = el("div", "schemes");
    schemes.append(el("p", "try-label", "Supported schemes"));
    const row = el("div", "chip-row center");
    for (const s of meta.schemes) {
      const chip = el("button", "chip");
      chip.type = "button";
      chip.setAttribute("aria-label", `Ask about ${s.name}`);
      chip.append(el("span", "dot"), el("span", null, s.name.replace(/^HDFC /, "")));
      chip.addEventListener("click", () => {
        setComposerText(`What is the expense ratio of ${s.name}?`);
        focusComposer();
      });
      row.append(chip);
    }
    schemes.append(row);
    wrap.append(schemes);
  }

  const note = el("p", "welcome-note");
  note.append(el("span", null, "Source: Groww scheme pages"), el("span", null, "·"),
    el("span", null, "No personal advice"));
  wrap.append(note);
  return wrap;
}

function userMessage(m) {
  const wrap = el("div", "msg msg-user");
  wrap.append(el("div", "bubble", m.text));
  wrap.append(el("span", "msg-time", m.local ? "Sending…" : clockTime(m.created_at)));
  return wrap;
}

function cardHead(kindInfo, timeText) {
  const head = el("header", "card-head");
  const who = el("span", "who");
  who.append(icon("logo"), el("span", null, "Fund Facts"));
  const badge = el("span", "badge");
  badge.append(icon(kindInfo.icon), el("span", null, kindInfo.label));
  head.append(who, badge);
  if (timeText) head.append(el("span", "msg-time", timeText));
  return head;
}

function assistantCard(m, msgs, index) {
  const info = KINDS[m.kind] || KINDS.answer;
  const card = el("article", `card ${info.tone}`.trim());
  card.setAttribute("aria-label", `${info.label} reply`);
  card.append(cardHead(info, clockTime(m.created_at)));
  card.append(el("p", "card-text", m.text));

  if (m.kind === "clarify" && state.meta && state.meta.schemes) {
    const row = el("div", "chip-row");
    for (const s of state.meta.schemes) {
      const chip = el("button", "chip", s.name);
      chip.type = "button";
      chip.addEventListener("click", () => send(s.name));
      row.append(chip);
    }
    card.append(row);
  }

  if (m.kind === "unavailable") {
    const previous = [...msgs.slice(0, index)].reverse().find((x) => x.role === "user");
    if (previous) {
      const actions = el("div", "card-actions");
      const retry = el("button", "btn btn-secondary btn-sm");
      retry.type = "button";
      retry.append(icon("refresh"), el("span", null, "Try again"));
      retry.addEventListener("click", () => send(previous.text));
      actions.append(retry);
      card.append(actions);
    }
  }

  if (m.source_url) {
    const foot = el("footer", "card-foot");
    const link = el("a", "source");
    link.href = m.source_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.append(icon("external"), el("span", "source-label", "Source:"),
      el("span", "source-url", shortUrl(m.source_url)));
    link.title = m.source_url;
    foot.append(link);
    if (m.footer) foot.append(el("p", "footer-line", m.footer));
    card.append(foot);
  }
  return card;
}

function thinkingCard(label) {
  const card = el("div", "card thinking");
  card.setAttribute("role", "status");
  const head = el("div", "thinking-head");
  const dots = el("span", "dots");
  dots.append(el("i"), el("i"), el("i"));
  head.append(el("span", null, label), dots);
  card.append(head, el("div", "skeleton w80"), el("div", "skeleton w60"), el("div", "skeleton w40"));
  return card;
}

function errorCard(error) {
  const card = el("div", "card tone-coral");
  card.setAttribute("role", "alert");
  card.append(cardHead({ label: "Not sent", icon: "alert" }, ""));
  card.append(el("p", "card-text", error.message));
  if (error.retryText) {
    const actions = el("div", "card-actions");
    const retry = el("button", "btn btn-secondary btn-sm");
    retry.type = "button";
    retry.append(icon("refresh"), el("span", null, "Try again"));
    retry.addEventListener("click", () => {
      state.drafts.delete(state.activeId);
      setComposerText("");
      send(error.retryText);
    });
    actions.append(retry);
    card.append(actions);
  }
  return card;
}

// ------------------------------------------------------------------ composer

const input = () => $("composer-input");

function setComposerText(text) {
  input().value = text || "";
  autosize();
  renderComposer();
}

function saveDraft() {
  if (state.activeId) {
    const value = input().value;
    if (value) state.drafts.set(state.activeId, value);
    else state.drafts.delete(state.activeId);
  }
}

function restoreDraft() {
  setComposerText(state.drafts.get(state.activeId) || "");
  hideComposerWarning();
}

function focusComposer() {
  if (window.matchMedia("(min-width: 768px)").matches) input().focus();
}

function autosize() {
  const t = input();
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
}

function renderComposer() {
  const pending = state.pending.has(state.activeId);
  const length = input().value.trim().length;
  const counter = $("char-counter");
  counter.textContent = `${input().value.length}/${maxChars()}`;
  counter.classList.toggle("over", input().value.length > maxChars());
  $("send-btn").disabled = pending || !length || input().value.length > maxChars() || !state.activeId;
  input().setAttribute("aria-busy", String(pending));
}

function showComposerWarning(text) {
  const box = $("composer-warning");
  box.replaceChildren(icon("lock"), el("span", null, text));
  box.hidden = false;
}

function hideComposerWarning() {
  $("composer-warning").hidden = true;
}

function showGlobalError(err) {
  if (state.activeId) state.errors.set(state.activeId, { message: err.message });
  render();
}

// ------------------------------------------------------------------ drawer (mobile)

function openDrawer() {
  $("app").classList.add("drawer-open");
  $("scrim").hidden = false;
  $("open-drawer").setAttribute("aria-expanded", "true");
  $("new-chat").focus();
}

function closeDrawer() {
  if (!$("app").classList.contains("drawer-open")) return;
  $("app").classList.remove("drawer-open");
  $("scrim").hidden = true;
  $("open-drawer").setAttribute("aria-expanded", "false");
}

// ------------------------------------------------------------------ status

function setStatus(health) {
  const status = $("service-status");
  const text = status.querySelector("span:last-child");
  status.classList.remove("ok", "warn", "down");
  if (!health) {
    status.classList.add("down");
    text.textContent = "Service unreachable";
  } else if (health.status !== "ok") {
    status.classList.add("warn");
    text.textContent = "Knowledge base not ready";
  } else if (!health.llm_configured) {
    status.classList.add("warn");
    text.textContent = "Answers limited (no LLM key)";
  } else {
    status.classList.add("ok");
    text.textContent = "Service online";
  }
}

// ------------------------------------------------------------------ start-up

function bindEvents() {
  $("new-chat").addEventListener("click", newChat);
  $("new-chat-mobile").addEventListener("click", newChat);
  $("open-drawer").addEventListener("click", openDrawer);
  $("close-drawer").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);

  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    send(input().value);
  });
  input().addEventListener("input", () => {
    autosize();
    renderComposer();
    hideComposerWarning();
  });
  input().addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input().value);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (state.confirmDelete) { state.confirmDelete = null; renderSidebar(); }
      closeDrawer();
    }
  });
  window.setInterval(() => renderSidebar(), 60000);   // refresh "x min ago"
}

function fitPlaceholder() {
  // The long placeholder wraps to two lines on phones; use a shorter one there.
  input().placeholder = window.matchMedia("(max-width: 480px)").matches
    ? "Ask a factual question…"
    : "Ask a factual question about HDFC mutual funds…";
  autosize();
}

async function init() {
  hydrateIcons();
  bindEvents();
  watchScroll();
  fitPlaceholder();
  window.matchMedia("(max-width: 480px)").addEventListener("change", fitPlaceholder);
  const [meta, health] = await Promise.allSettled([api.meta(), api.health()]);
  state.meta = meta.status === "fulfilled" ? meta.value : null;
  setStatus(health.status === "fulfilled" ? health.value : null);
  if (state.meta) {
    $("disclaimer-text").textContent = state.meta.disclaimer;
    if (state.meta.corpus_last_updated) {
      $("data-date-text").textContent = `Data as on ${displayDate(state.meta.corpus_last_updated)}`;
      $("data-date").hidden = false;
    }
    input().setAttribute("maxlength", String(maxChars() + 100));
  }

  try {
    const ids = loadIds();
    if (ids.length) {
      const known = await api.listThreads(ids);
      state.threads = known.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      saveIds();   // forget ids the server no longer has (THR-07)
    }
    if (state.threads.length) await selectThread(state.threads[0].id);
    else await selectThread((await createThread()).id);   // UI-08
  } catch (err) {
    state.errors.set("boot", { message: err.message });
    state.activeId = "boot";
    state.loaded.add("boot");
    render();
  }
}

init();
