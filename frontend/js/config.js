// Runtime configuration. This default (same-origin API) is used locally and when the
// FastAPI backend serves the UI. The Vercel build (frontend/build.sh) replaces this file
// with one pointing at the Render backend, from the API_BASE_URL environment variable.
window.FUND_FACTS_CONFIG = { apiBase: "" };
