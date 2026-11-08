// Thin client for the FastAPI backend (same origin).

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return null;
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* non-JSON error page */
  }
  if (!response.ok) {
    let message = "Something went wrong. Please try again.";
    const detail = data && data.detail;
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail) && detail[0] && detail[0].msg) {
      message = detail[0].msg.replace(/^Value error, /, "");
      message = message.charAt(0).toUpperCase() + message.slice(1) + ".";
    }
    throw new ApiError(response.status, message);
  }
  return data;
}

export const api = {
  meta: () => request("GET", "/api/meta"),
  health: () => request("GET", "/api/health"),
  createThread: () => request("POST", "/api/threads"),
  listThreads: (ids) => request("GET", `/api/threads?ids=${encodeURIComponent(ids.join(","))}`),
  messages: (id) => request("GET", `/api/threads/${id}/messages?limit=200`),
  send: (id, text) => request("POST", `/api/threads/${id}/messages`, { text }),
  deleteThread: (id) => request("DELETE", `/api/threads/${id}`),
};
