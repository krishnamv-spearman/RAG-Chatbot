// Inline SVG icons (stroke = currentColor). Static strings only: never built from user data.
const svg = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false">${body}</svg>`;

export const ICONS = {
  logo: svg('<path d="M3 10 12 4l9 6"/><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
  menu: svg('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  send: svg('<path d="M12 19V5M6 11l6-6 6 6"/>'),
  trash: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>'),
  shield: svg('<path d="M12 3 5 6v6c0 4.2 3 7.6 7 9 4-1.4 7-4.8 7-9V6l-7-3Z"/>'),
  check: svg('<path d="M12 3 5 6v6c0 4.2 3 7.6 7 9 4-1.4 7-4.8 7-9V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>'),
  chart: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
  help: svg('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.6v.6M12 17h.01"/>'),
  search: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  ban: svg('<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>'),
  lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
  alert: svg('<path d="M12 4 2.5 20h19L12 4Z"/><path d="M12 10v4M12 17h.01"/>'),
  external: svg('<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
  refresh: svg('<path d="M20 11a8 8 0 0 0-14.9-4M4 4v4h4M4 13a8 8 0 0 0 14.9 4M20 20v-4h-4"/>'),
  sparkle: svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'),
  clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  doc: svg('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/>'),
};

export function icon(name) {
  const span = document.createElement("span");
  span.setAttribute("data-icon", name);
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = ICONS[name] || "";
  return span;
}

/** Fill every static `[data-icon]` placeholder in the page. */
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    if (!el.firstChild && ICONS[el.dataset.icon]) el.innerHTML = ICONS[el.dataset.icon];
  });
}
