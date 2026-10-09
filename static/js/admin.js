let userPage = 1,
  userPages = 1;
async function refreshAdmin() {
  const d = await api("/api/admin/stats");
  $("global-total").textContent = d.total;
  $("global-fraud").textContent = d.fraud_rate + "%";
  $("cpu").textContent = d.cpu_percent + "%";
  $("ram").textContent = d.ram_percent + "%";
  for (const m of ["precision", "recall", "f1"])
    $(m).textContent = (d.model.metrics[m] * 100).toFixed(1) + "%";
  $("model-version").textContent = d.model.version;
  $("trained-at").textContent = dateLabel(d.model.trained_at);
  $("next-run").textContent = dateLabel(d.training.next_run);
  $("training-state").textContent = d.training.status;
  $("retrain").disabled = ["queued", "running"].includes(d.training.status);
  $("text-ms").textContent =
    d.text_p95 === null ? "No samples" : d.text_p95.toFixed(1) + " ms";
  $("image-ms").textContent =
    d.image_p95 === null
      ? "No samples"
      : (d.image_p95 / 1000).toFixed(2) + " s";
  $("ocr-health").textContent = d.ocr_available ? "Available" : "Not installed";
  $("admin-status").textContent =
    d.training.error || "System statistics refresh every 10 seconds.";
  $("domains").innerHTML = d.domains.length
    ? d.domains
        .map(
          (x) =>
            `<div class="info-row"><span>${escapeHTML(x.domain)}</span><strong>${x.count} flagged scans</strong></div>`,
        )
        .join("")
    : '<p class="empty">No suspicious domains recorded.</p>';
  $("feedback-count").textContent = d.pending_feedback + " NEW";
  $("feedback-list").innerHTML = d.feedback.length
    ? d.feedback
        .map(
          (x) =>
            `<div class="info-row"><span>Scan #${x.scan_id}<br>${escapeHTML(dateLabel(x.created_at))}</span><strong>${escapeHTML(x.label)}<br><small>${x.trained ? "Included in model" : "Pending training"}</small></strong></div>`,
        )
        .join("")
    : '<p class="empty">No corrections yet.</p>';
}
async function refreshUsers() {
  const d = await api(
    "/api/admin/users?page=" +
      userPage +
      "&q=" +
      encodeURIComponent($("user-search").value),
  );
  userPages = d.pages;
  $("user-page").textContent = userPage;
  $("user-prev").disabled = userPage <= 1;
  $("user-next").disabled = userPage >= userPages;
  $("users").innerHTML =
    d.items
      .map(
        (u) =>
          `<tr><td>${escapeHTML(u.name)}</td><td>${escapeHTML(u.email)}</td><td><span class="badge">${escapeHTML(u.role)}</span></td><td>${escapeHTML(dateLabel(u.created_at))}</td><td>${u.id === window.currentUserId ? "Current account" : `<button class="text-button" data-role="${u.id}" data-next="${u.role === "admin" ? "user" : "admin"}">${u.role === "admin" ? "Demote" : "Promote to admin"}</button> &nbsp; <button class="text-button red" data-delete="${u.id}">Delete</button>`}</td></tr>`,
      )
      .join("") ||
    '<tr><td colspan="5" class="empty">No matching members.</td></tr>';
  $("users")
    .querySelectorAll("[data-role]")
    .forEach(
      (b) =>
        (b.onclick = async () => {
          if (!confirm(`Change this member's role to ${b.dataset.next}?`))
            return;
          try {
            await api("/api/admin/users/" + b.dataset.role, {
              method: "PATCH",
              body: JSON.stringify({ role: b.dataset.next }),
            });
            await refreshUsers();
            toast("Role updated. Existing sessions have been revoked.");
          } catch (e) {
            toast(e.message);
          }
        }),
    );
  $("users")
    .querySelectorAll("[data-delete]")
    .forEach(
      (b) =>
        (b.onclick = async () => {
          if (
            !confirm(
              "Permanently delete this member, their scans, and feedback?",
            )
          )
            return;
          try {
            await api("/api/admin/users/" + b.dataset.delete, {
              method: "DELETE",
            });
            await refreshUsers();
            toast("Member deleted. Retrain to remove their training examples.");
          } catch (e) {
            toast(e.message);
          }
        }),
    );
}
$("retrain").onclick = async () => {
  $("retrain").disabled = true;
  try {
    await api("/api/admin/retrain", { method: "POST", body: "{}" });
    toast("Retraining started in the background.");
    await refreshAdmin();
  } catch (e) {
    toast(e.message);
    $("retrain").disabled = false;
  }
};
let timer;
$("user-search").oninput = () => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    userPage = 1;
    refreshUsers().catch((e) => toast(e.message));
  }, 300);
};
$("user-prev").onclick = () => {
  userPage--;
  refreshUsers().catch((e) => toast(e.message));
};
$("user-next").onclick = () => {
  userPage++;
  refreshUsers().catch((e) => toast(e.message));
};
Promise.all([refreshAdmin(), refreshUsers()]).catch((e) => toast(e.message));
setInterval(() => {
  if (!document.hidden) refreshAdmin().catch((e) => toast(e.message));
}, 10000);
