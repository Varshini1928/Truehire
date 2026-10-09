let latestScan = null,
  page = 1,
  totalPages = 1,
  ocrBusy = false,
  scanning = false,
  ocrText = "",
  ocrMetrics = null,
  activeOcr = null;
let chartFraud = makeChart("fraud-chart", {
  type: "doughnut",
  data: {
    labels: ["Low risk", "Flagged", "Needs review"],
    datasets: [
      {
        data: [0, 0, 0],
        backgroundColor: ["#64d5ae", "#fa7d93", "#ecc17b"],
        borderWidth: 0,
        borderRadius: 3,
        spacing: 3,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    cutout: "80%",
    plugins: { legend: { display: false } },
  },
});
let chartActivity = makeChart("activity-chart", {
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label: "Scans",
        data: [],
        borderColor: "#70ece0",
        backgroundColor: "#70ece013",
        fill: true,
        tension: 0.35,
        pointRadius: 2,
        borderWidth: 2,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: "#79909f", font: { size: 9 }, maxTicksLimit: 7 },
      },
      y: {
        beginAtZero: true,
        grid: { color: "#27354066" },
        ticks: { color: "#79909f", precision: 0, font: { size: 9 } },
      },
    },
  },
});
document.querySelectorAll("[data-tab]").forEach(
  (button) =>
    (button.onclick = () => {
      document.querySelectorAll("[data-tab]").forEach((b) => {
        b.classList.toggle("selected", b === button);
        b.setAttribute("aria-selected", String(b === button));
        $("tab-" + b.dataset.tab).hidden = b !== button;
      });
    }),
);
$("job-text").oninput = () => {
  $("characters").textContent =
    `${$("job-text").value.length.toLocaleString()} / 20,000`;
  updateInputs();
};
$("job-url").oninput = updateInputs;
$("extracted").oninput = () => {
  ocrText = $("extracted").value;
  updateInputs();
};
function updateInputs() {
  const inputs = [
    $("job-text").value.trim() ? "description" : null,
    ocrText.trim() ? "screenshot text" : null,
    $("job-url").value.trim() ? "link" : null,
  ].filter(Boolean);
  $("input-summary").textContent = inputs.length
    ? `Included: ${inputs.join(" + ")}`
    : "Text, screenshot & link can be combined";
}
$("example").onclick = () => {
  $("job-text").value =
    "Remote data entry associate. Earn ₹80,000 weekly with no experience. No interview required. Pay registration fee of ₹2,000 to reserve your position. Contact the recruiter on Telegram today.";
  $("job-text").oninput();
};
function setBusy() {
  $("scan-button").disabled = scanning || ocrBusy;
  $("scan-button").textContent = scanning
    ? "Analyzing…"
    : ocrBusy
      ? "Extracting screenshot…"
      : "⌕  Analyze opportunity";
}
async function pollJob(id, onUpdate, signal) {
  while (true) {
    if (signal?.aborted) throw Error("Cancelled");
    const job = await api("/api/jobs/" + id, { signal });
    onUpdate?.(job);
    if (job.status === "completed") return job.result;
    if (job.status === "failed")
      throw Error(job.error || "Processing failed. Please try again.");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
async function uploadImage(file) {
  if (!file) return;
  if (
    !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
    file.size > 5 * 1024 * 1024
  ) {
    toast("Choose a PNG, JPG, or WEBP file up to 5 MB.");
    return;
  }
  activeOcr?.abort();
  const controller = new AbortController();
  activeOcr = controller;
  ocrBusy = true;
  ocrText = "";
  ocrMetrics = null;
  $("extracted").value = "";
  setBusy();
  $("image-status").textContent = "Uploading screenshot…";
  try {
    const form = new FormData();
    form.append("image", file);
    const job = await api("/api/ocr", {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    const result = await pollJob(
      job.id,
      (j) => {
        $("image-status").textContent =
          j.logs.at(-1)?.message || "Extracting text…";
      },
      controller.signal,
    );
    ocrText = result.text;
    ocrMetrics = { job_id: job.id };
    $("extracted").value = ocrText;
    $("image-status").textContent =
      `Text extracted · ${ocrText.length} characters · ${(result.duration_ms / 1000).toFixed(2)}s`;
    updateInputs();
  } catch (e) {
    if (!controller.signal.aborted) {
      $("image-status").textContent = e.message;
      toast(e.message);
    }
  } finally {
    if (activeOcr === controller) {
      ocrBusy = false;
      setBusy();
    }
  }
}
$("job-image").onchange = (e) => uploadImage(e.target.files[0]);
const drop = $("dropzone");
["dragenter", "dragover"].forEach((name) =>
  drop.addEventListener(name, (e) => {
    e.preventDefault();
    drop.classList.add("dragging");
  }),
);
["dragleave", "drop"].forEach((name) =>
  drop.addEventListener(name, (e) => {
    e.preventDefault();
    drop.classList.remove("dragging");
  }),
);
drop.addEventListener("drop", (e) => uploadImage(e.dataTransfer.files[0]));
$("clear-image").onclick = () => {
  activeOcr?.abort();
  activeOcr = null;
  ocrBusy = false;
  ocrText = "";
  ocrMetrics = null;
  $("extracted").value = "";
  $("job-image").value = "";
  $("image-status").textContent = "";
  setBusy();
  updateInputs();
};
$("scan-form").onsubmit = async (event) => {
  event.preventDefault();
  if (scanning || ocrBusy) return;
  const payload = {
    text: $("job-text").value.trim(),
    image_text: ocrText.trim(),
    url: $("job-url").value.trim(),
    ocr_job_id: ocrMetrics?.job_id,
  };
  if (!payload.text && !payload.image_text && !payload.url) {
    toast("Add a description, screenshot, or link first.");
    return;
  }
  scanning = true;
  setBusy();
  $("progress").hidden = false;
  $("result").hidden = true;
  $("scan-logs").textContent = "";
  try {
    const job = await api("/api/scans", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const result = await pollJob(job.id, (j) => {
      $("scan-logs").replaceChildren(
        ...j.logs.map((log) =>
          Object.assign(document.createElement("li"), {
            textContent: `${log.level === "warning" ? "⚠" : "✓"} ${log.message}`,
          }),
        ),
      );
    });
    showResult(result);
    await Promise.all([refreshStats(), refreshHistory()]);
  } catch (e) {
    toast(e.message);
    $("scan-logs").append(
      Object.assign(document.createElement("li"), { textContent: e.message }),
    );
  } finally {
    scanning = false;
    setBusy();
    $("progress").hidden = true;
  }
};
function showResult(scan) {
  latestScan = scan;
  $("result").hidden = false;
  $("verdict").textContent = {
    fake: "High-risk signals detected",
    real: "No strong risk signals found",
    review: "This opportunity needs review",
  }[scan.verdict];
  const color =
    scan.verdict === "fake"
      ? "#fa7d93"
      : scan.verdict === "review"
        ? "#ecc17b"
        : "#70ece0";
  $("verdict").style.color = color;
  $("confidence").textContent =
    scan.confidence === null ? "—" : `${scan.confidence}%`;
  $("score-ring").style.borderColor = color;
  $("score-ring").querySelector("small").textContent =
    scan.confidence === null ? "rule-based flag" : "model confidence";
  $("result-summary").textContent = scan.override
    ? "A policy rule determined this assessment."
    : scan.confidence === null
      ? "Not enough reliable evidence for a model score."
      : "Combined model and link risk assessment.";
  $("confidence-bar").style.background = color;
  $("confidence-bar").style.width = (scan.confidence ?? 100) + "%";
  $("reasons").replaceChildren(
    ...scan.reasons.map((reason) =>
      Object.assign(document.createElement("li"), { textContent: reason }),
    ),
  );
  $("feedback-button").disabled = !!scan.feedback;
  $("feedback-button").textContent = scan.feedback
    ? "Correction received"
    : "Submit correction";
  $("correction").value = scan.verdict === "fake" ? "real" : "fake";
}
$("feedback-button").onclick = async () => {
  if (!latestScan) return;
  $("feedback-button").disabled = true;
  try {
    await api("/api/scans/" + latestScan.id + "/feedback", {
      method: "POST",
      body: JSON.stringify({ label: $("correction").value }),
    });
    $("feedback-button").textContent = "Correction received";
    toast("Your correction has been saved. Thank you.");
  } catch (e) {
    $("feedback-button").disabled = false;
    toast(e.message);
  }
};
async function refreshStats() {
  const s = await api("/api/stats");
  $("total").textContent = s.total;
  $("real").textContent = s.real;
  $("fake").textContent = s.fake;
  $("latency").textContent = s.total
    ? `${(s.average_ms / 1000).toFixed(2)}s`
    : "—";
  $("legend-real").textContent = s.real;
  $("legend-fake").textContent = s.fake;
  $("legend-review").textContent = s.review;
  $("fraud-rate").textContent = s.total ? `${s.fraud_rate}%` : "—";
  $("threat-level").textContent = !s.total
    ? "No data"
    : s.recent_rate > 60
      ? "High"
      : s.recent_rate > 25
        ? "Elevated"
        : "Low";
  $("threat-marker").style.left = `${Math.min(99, s.recent_rate)}%`;
  if (chartFraud) {
    chartFraud.data.datasets[0].data = [s.real, s.fake, s.review];
    chartFraud.update();
  }
  if (chartActivity) {
    chartActivity.data.labels = s.daily.map((d) => d.date.slice(5));
    chartActivity.data.datasets[0].data = s.daily.map((d) => d.count);
    chartActivity.update();
  }
}
async function refreshHistory() {
  const s = await api(
    `/api/scans?page=${page}&q=${encodeURIComponent($("history-search").value)}&verdict=${$("history-filter").value}`,
  );
  totalPages = s.pages;
  $("history-body").innerHTML = s.items.length
    ? s.items
        .map(
          (scan) =>
            `<tr data-id="${scan.id}" tabindex="0" aria-label="Open scan ${scan.id}" style="cursor:pointer"><td>${escapeHTML(scan.text || scan.url || "Screenshot analysis")}</td><td>${escapeHTML(scan.source)}</td><td>${pill(scan.verdict)}</td><td>${scan.confidence === null ? "Rule / insufficient data" : scan.confidence + "%"}</td><td>${escapeHTML(dateLabel(scan.created_at))}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="5" class="empty">No opportunities here yet. Run a scan to build your history.</td></tr>';
  $("history-count").textContent = `${s.total} opportunities`;
  $("page-number").textContent = page;
  $("previous").disabled = page === 1;
  $("next").disabled = page >= totalPages;
  $("history-body")
    .querySelectorAll("[data-id]")
    .forEach((row) => {
      const open = async () => {
        try {
          showResult(await api("/api/scans/" + row.dataset.id));
          $("result").scrollIntoView({ behavior: "smooth", block: "center" });
        } catch (e) {
          toast(e.message);
        }
      };
      row.onclick = open;
      row.onkeydown = (e) => {
        if (e.key === "Enter") open();
      };
    });
}
let debounce;
$("history-search").oninput = () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    page = 1;
    refreshHistory().catch((e) => toast(e.message));
  }, 300);
};
$("history-filter").onchange = () => {
  page = 1;
  refreshHistory().catch((e) => toast(e.message));
};
$("previous").onclick = () => {
  if (page > 1) {
    page--;
    refreshHistory().catch((e) => toast(e.message));
  }
};
$("next").onclick = () => {
  if (page < totalPages) {
    page++;
    refreshHistory().catch((e) => toast(e.message));
  }
};
Promise.all([refreshStats(), refreshHistory()]).catch((e) => toast(e.message));
