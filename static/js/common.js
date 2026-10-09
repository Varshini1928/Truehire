// JWT stays in an HttpOnly cookie. A separate CSRF cookie protects mutations.
const $ = (id) => document.getElementById(id);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function csrf() {
  return (
    document.cookie
      .split("; ")
      .find((v) => v.startsWith("csrf_token="))
      ?.split("=")
      .slice(1)
      .join("=") || ""
  );
}
async function api(url, options = {}) {
  const headers = {
    ...(options.body instanceof FormData
      ? {}
      : { "Content-Type": "application/json" }),
    "X-CSRF-Token": csrf(),
    ...options.headers,
  };
  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  const data = await response
    .json()
    .catch(() => ({ error: "The server could not complete this request." }));
  if (response.status === 401 && !url.includes("/auth/")) {
    location.href = "/login";
    throw Error("Please sign in again.");
  }
  if (!response.ok) throw Error(data.error || "Request failed.");
  return data;
}
function toast(message) {
  const el = $("toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => (el.hidden = true), 5000);
}
async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
    location.href = "/login";
  } catch (e) {
    toast(e.message);
  }
}
function dateLabel(value) {
  return value ? new Date(value).toLocaleString() : "—";
}
function pill(label) {
  return `<span class="pill ${escapeHTML(label)}">${{ real: "Low risk", fake: "Flagged", review: "Needs review" }[label] || "Unknown"}</span>`;
}
function makeChart(id, config) {
  if (!window.Chart) {
    $(id).replaceWith(
      Object.assign(document.createElement("p"), {
        textContent: "Chart library unavailable. Counts are shown above.",
      }),
    );
    return null;
  }
  return new Chart($(id), config);
}
