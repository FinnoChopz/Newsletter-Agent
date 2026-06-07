const state = {
  profiles: [],
  activeProfileId: "",
  candidates: [],
  recommendations: [],
  scheduler: { installed: false, path: "" },
};

window.finnSignalState = state;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed with ${response.status}`);
  }
  return data;
}

function activeProfile() {
  return state.profiles.find((profile) => profile.id === state.activeProfileId) || null;
}

function setBusy(button, busyText) {
  if (!button) {
    return () => {};
  }

  const original = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  return () => {
    button.disabled = false;
    button.textContent = original;
  };
}

function submitButtonFor(form, event) {
  return event.submitter || form.querySelector('button[type="submit"]');
}

function requireProfile() {
  const profile = activeProfile();
  if (!profile) {
    throw new Error("Create or select a profile first.");
  }
  return profile;
}

function renderProfiles() {
  const select = $("#profileSelect");
  select.innerHTML = "";

  if (!state.profiles.length) {
    select.innerHTML = '<option value="">No profiles yet</option>';
    state.activeProfileId = "";
    return;
  }

  if (!state.activeProfileId) {
    state.activeProfileId = state.profiles[0].id;
  }

  state.profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = `${profile.display_name} (${profile.email})`;
    option.selected = profile.id === state.activeProfileId;
    select.appendChild(option);
  });
}

function renderStatus() {
  const profile = activeProfile();
  const schedule = profile?.schedule || {};
  $("#statusStrip").innerHTML = `
    <div class="metric"><span>Gmail</span><strong>${profile?.gmail_connected ? "Connected" : "Not connected"}</strong></div>
    <div class="metric"><span>Sources</span><strong>${profile?.source_count || 0}</strong></div>
    <div class="metric"><span>Delivery</span><strong>${schedule.enabled === false ? "Paused" : schedule.time || "11:00"}</strong></div>
    <div class="metric"><span>Scheduler</span><strong>${state.scheduler.installed ? "Installed" : "Not installed"}</strong></div>
  `;

  $("#schedulerPill").textContent = state.scheduler.installed ? "Installed" : "Not installed";
  $("#sourceCount").textContent = String(profile?.source_count || 0);

  if (profile) {
    const form = $("#scheduleForm");
    form.elements.time.value = schedule.time || "11:00";
    form.elements.frequency.value = schedule.frequency || "daily";
    form.elements.enabled.checked = schedule.enabled !== false;
  }
}

function candidateClass(candidate) {
  if (candidate.classification === "newsletter" && candidate.should_include) return "good";
  if (candidate.classification === "unclear") return "warn";
  return "bad";
}

function renderCandidates(candidates) {
  const list = $("#candidateList");
  if (!candidates.length) {
    list.innerHTML = '<p class="quiet">No candidates loaded.</p>';
    return;
  }

  list.innerHTML = candidates
    .map((candidate, index) => {
      const checked = candidate.classification === "newsletter" && candidate.should_include ? "checked" : "";
      const examples = (candidate.example_subjects || []).slice(0, 3).join(" | ");
      return `
        <article class="item ${candidateClass(candidate)}">
          <div class="item-row">
            <label class="checkline">
              <input type="checkbox" data-candidate="${index}" ${checked}>
              <span><strong>${escapeHtml(candidate.name || candidate.sender)}</strong></span>
            </label>
            <div class="meta">
              <span>${escapeHtml(candidate.classification || "candidate")}</span>
              <span>${Math.round((candidate.confidence || 0) * 100)}%</span>
              <span>${candidate.count || 0} emails</span>
            </div>
          </div>
          <p>${escapeHtml(candidate.reason || "")}</p>
          <div class="source-senders">${escapeHtml(candidate.sender || "")}</div>
          <p>${escapeHtml(examples)}</p>
        </article>
      `;
    })
    .join("");
}

function renderSources(sources) {
  const list = $("#sourceList");
  if (!sources.length) {
    list.innerHTML = '<p class="quiet">No approved sources yet.</p>';
    return;
  }

  list.innerHTML = sources
    .map((source) => {
      const sender = (source.senders || [])[0] || "";
      const statusClass = source.enabled === false ? "bad" : source.status === "needs_subscription" ? "warn" : "good";
      const status = source.enabled === false ? "Off" : source.status === "needs_subscription" ? "Needs subscription" : "On";
      const link = source.subscription_url
        ? `<a href="${escapeAttr(source.subscription_url)}" target="_blank" rel="noreferrer">Subscribe</a>`
        : "";
      return `
        <article class="item ${statusClass}">
          <div class="item-row">
            <div>
              <h3>${escapeHtml(source.name || sender)}</h3>
              <div class="source-senders">${escapeHtml((source.senders || []).join(", "))}</div>
            </div>
            <button class="secondary" data-toggle-source="${escapeAttr(sender)}" data-enabled="${source.enabled === false ? "true" : "false"}">
              ${source.enabled === false ? "Turn on" : "Turn off"}
            </button>
          </div>
          <div class="meta">
            <span>${status}</span>
            <span>${escapeHtml(source.source_type || "manual")}</span>
            ${link ? `<span>${link}</span>` : ""}
          </div>
          ${source.reason ? `<p>${escapeHtml(source.reason)}</p>` : ""}
        </article>
      `;
    })
    .join("");
}

function renderRecommendations(recommendations) {
  const list = $("#recommendationList");
  if (!recommendations.length) {
    list.innerHTML = '<p class="quiet">No recommendations loaded.</p>';
    return;
  }

  list.innerHTML = recommendations
    .map((rec, index) => `
      <article class="item good">
        <div class="item-row">
          <div>
            <h3>${escapeHtml(rec.name)}</h3>
            <p>${escapeHtml(rec.description || "")}</p>
          </div>
          <button class="secondary" data-add-rec="${index}">Add</button>
        </div>
        <p>${escapeHtml(rec.why_relevant || "")}</p>
        <div class="source-senders">${escapeHtml((rec.likely_senders || []).join(", "))}</div>
        <div class="meta">
          <span>${Math.round((rec.confidence || 0) * 100)}%</span>
          ${(rec.topics || []).slice(0, 4).map((topic) => `<span>${escapeHtml(topic)}</span>`).join("")}
          ${rec.subscription_url ? `<span><a href="${escapeAttr(rec.subscription_url)}" target="_blank" rel="noreferrer">Subscribe</a></span>` : ""}
        </div>
      </article>
    `)
    .join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

async function loadState() {
  const selectedBeforeRefresh = $("#profileSelect")?.value || state.activeProfileId;
  const data = await api("/api/state");
  state.profiles = data.profiles || [];
  state.scheduler = data.scheduler || state.scheduler;
  if (selectedBeforeRefresh && state.profiles.some((profile) => profile.id === selectedBeforeRefresh)) {
    state.activeProfileId = selectedBeforeRefresh;
  }
  renderProfiles();
  renderStatus();
  await loadSources();
}

async function loadSources() {
  const profile = activeProfile();
  if (!profile) {
    renderSources([]);
    return;
  }
  const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/sources`);
  renderSources(data.sources || []);
}

function showResult(selector, value) {
  $(selector).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function wireTabs() {
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".tab").forEach((tab) => tab.classList.remove("is-active"));
      $$(".view").forEach((view) => view.classList.remove("is-active"));
      button.classList.add("is-active");
      $(`#${button.dataset.tab}`).classList.add("is-active");
    });
  });
}

function wireForms() {
  $("#profileSelect").addEventListener("change", async (event) => {
    state.activeProfileId = event.target.value;
    state.candidates = [];
    state.recommendations = [];
    renderCandidates([]);
    renderRecommendations([]);
    renderStatus();
    await loadSources();
  });

  $("#profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const done = setBusy(submitButtonFor(formElement, event), "Creating...");
    try {
      const form = new FormData(formElement);
      const data = await api("/api/profiles", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(form.entries())),
      });
      state.activeProfileId = data.profile.id;
      formElement.reset();
      await loadState();
    } catch (error) {
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#connectGmail").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Opening...");
    try {
      const profile = requireProfile();
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/oauth/start`, { method: "POST", body: "{}" });
      window.open(data.auth_url, "_blank", "noopener,noreferrer");
      showResult("#gmailResult", "After Google says Gmail is connected, return here and refresh status.");
    } catch (error) {
      showResult("#gmailResult", error.message);
    } finally {
      done();
    }
  });

  $("#scanInbox").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Scanning...");
    try {
      const profile = requireProfile();
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/scan`, {
        method: "POST",
        body: JSON.stringify({
          days: Number($("#scanDays").value || 30),
          max_results: Number($("#scanMax").value || 300),
        }),
      });
      state.candidates = data.candidates || [];
      renderCandidates(state.candidates);
    } catch (error) {
      renderCandidates([]);
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#importCandidates").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Saving...");
    try {
      const profile = requireProfile();
      const selected = $$("[data-candidate]:checked").map((input) => state.candidates[Number(input.dataset.candidate)]);
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/sources/import-candidates`, {
        method: "POST",
        body: JSON.stringify({ candidates: selected }),
      });
      renderSources(data.sources || []);
      await loadState();
    } catch (error) {
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#manualSourceForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const done = setBusy(submitButtonFor(formElement, event), "Adding...");
    try {
      const profile = requireProfile();
      const form = new FormData(formElement);
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/sources`, {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(form.entries())),
      });
      formElement.reset();
      renderSources(data.sources || []);
      await loadState();
    } catch (error) {
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#sourceList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-toggle-source]");
    if (!button) return;
    const done = setBusy(button, "Saving...");
    try {
      const profile = requireProfile();
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/sources/toggle`, {
        method: "POST",
        body: JSON.stringify({
          sender: button.dataset.toggleSource,
          enabled: button.dataset.enabled === "true",
        }),
      });
      renderSources(data.sources || []);
      await loadState();
    } catch (error) {
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#runDiscovery").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Finding...");
    try {
      const profile = requireProfile();
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/discover`, {
        method: "POST",
        body: JSON.stringify({
          query: $("#discoveryQuery").value,
          auto_add: $("#autoAddDiscovery").checked,
        }),
      });
      state.recommendations = data.recommendations || [];
      renderRecommendations(state.recommendations);
      renderSources(data.sources || []);
      await loadState();
    } catch (error) {
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#recommendationList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-add-rec]");
    if (!button) return;
    const done = setBusy(button, "Adding...");
    try {
      const profile = requireProfile();
      const recommendation = state.recommendations[Number(button.dataset.addRec)];
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/recommendations/add`, {
        method: "POST",
        body: JSON.stringify({ recommendation }),
      });
      renderSources(data.sources || []);
      await loadState();
    } catch (error) {
      alert(error.message);
    } finally {
      done();
    }
  });

  $("#scheduleForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const done = setBusy(submitButtonFor(formElement, event), "Saving...");
    try {
      const profile = requireProfile();
      const form = new FormData(formElement);
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/schedule`, {
        method: "POST",
        body: JSON.stringify({
          time: form.get("time"),
          frequency: form.get("frequency"),
          enabled: form.get("enabled") === "on",
        }),
      });
      state.profiles = state.profiles.map((item) => (item.id === data.profile.id ? data.profile : item));
      renderStatus();
      showResult("#scheduleResult", "Schedule saved.");
    } catch (error) {
      showResult("#scheduleResult", error.message);
    } finally {
      done();
    }
  });

  $("#installScheduler").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Installing...");
    try {
      const data = await api("/api/scheduler/install", { method: "POST", body: "{}" });
      showResult("#scheduleResult", data.scheduler);
      await loadState();
    } catch (error) {
      showResult("#scheduleResult", error.message);
    } finally {
      done();
    }
  });

  $("#sendTest").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Sending...");
    try {
      const profile = requireProfile();
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/send-test`, { method: "POST", body: "{}" });
      showResult("#runOutput", data.result);
    } catch (error) {
      showResult("#runOutput", error.message);
    } finally {
      done();
    }
  });
}

wireTabs();
wireForms();
loadState().catch((error) => {
  $("#statusStrip").innerHTML = `<div class="metric"><span>Error</span><strong>${escapeHtml(error.message)}</strong></div>`;
});
