const state = {
  profiles: [],
  activeProfileId: "",
  candidates: [],
  recommendations: [],
  rankings: null,
  rankingFilter: "all",
  scheduler: { installed: false, path: "" },
  storage: null,
};

window.finnSignalState = state;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const ACTIVE_PROFILE_STORAGE_KEY = "finnSignalActiveProfileId";

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

  const savedProfileId = window.localStorage.getItem(ACTIVE_PROFILE_STORAGE_KEY) || "";
  if (!state.activeProfileId && savedProfileId && state.profiles.some((profile) => profile.id === savedProfileId)) {
    state.activeProfileId = savedProfileId;
  }

  if (!state.activeProfileId || !state.profiles.some((profile) => profile.id === state.activeProfileId)) {
    state.activeProfileId = state.profiles[0].id;
  }

  state.profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = `${profile.display_name} (${profile.email})`;
    option.selected = profile.id === state.activeProfileId;
    select.appendChild(option);
  });
  window.localStorage.setItem(ACTIVE_PROFILE_STORAGE_KEY, state.activeProfileId);
  renderProfileForm();
}

function renderProfileForm() {
  const form = $("#profileForm");
  if (!form) return;

  const profile = activeProfile();
  if (!profile) {
    form.elements.display_name.value = "";
    form.elements.email.value = "";
    form.elements.subscription_email.value = "";
    form.elements.interests.value = "";
    return;
  }

  if (form.contains(document.activeElement)) {
    return;
  }

  form.elements.display_name.value = profile.display_name || "";
  form.elements.email.value = profile.email || "";
  form.elements.subscription_email.value = profile.subscription_email || "";
  form.elements.interests.value = (profile.interests || []).join("\n");
}

function renderStatus() {
  const profile = activeProfile();
  const schedule = profile?.schedule || {};
  const profileState = profile?.state || {};
  const schedulerLabel = schedulerStatusLabel();
  const lastSent = profileState.last_sent_at || "Never";
  const lastChecked = profileState.last_scheduler_check_at || "Never";
  const lastResult = profileState.last_run_status || profileState.last_scheduler_decision || "None";
  const receivingCount = profile?.source_count || 0;
  const pendingCount = profile?.pending_source_count || 0;
  $("#statusStrip").innerHTML = `
    <div class="metric"><span>Gmail</span><strong>${profile?.gmail_connected ? "Connected" : "Not connected"}</strong></div>
    <div class="metric"><span>Receiving</span><strong>${receivingCount}</strong></div>
    <div class="metric"><span>Pending</span><strong>${pendingCount}</strong></div>
    <div class="metric"><span>Delivery</span><strong>${schedule.enabled === false ? "Paused" : schedule.time || "11:00"}</strong></div>
    <div class="metric"><span>Runner</span><strong>${schedulerLabel}</strong></div>
    <div class="metric"><span>Last sent</span><strong>${escapeHtml(lastSent)}</strong></div>
    <div class="metric"><span>Last checked</span><strong>${escapeHtml(lastChecked)}</strong></div>
    <div class="metric"><span>Last result</span><strong>${escapeHtml(lastResult)}</strong></div>
  `;
  if (profileState.last_error) {
    $("#statusStrip").insertAdjacentHTML(
      "beforeend",
      `<div class="metric warning"><span>Last error</span><strong>${escapeHtml(profileState.last_error)}</strong></div>`
    );
  }

  $("#schedulerPill").textContent = schedulerLabel;
  const schedulerDetails = $("#schedulerDetails");
  if (schedulerDetails) {
    schedulerDetails.textContent = schedulerStatusDetail();
  }
  $("#sourceCount").textContent = pendingCount ? `${receivingCount} live / ${pendingCount} pending` : String(receivingCount);
  if (state.scheduler.hosted) {
    $("#scheduleResult").textContent = "";
  }

  if (profile) {
    const form = $("#scheduleForm");
    form.elements.time.value = schedule.time || "11:00";
    form.elements.frequency.value = schedule.frequency || "daily";
    form.elements.enabled.checked = schedule.enabled !== false;
  }
}

function schedulerStatusLabel() {
  if (state.scheduler.hosted) return "Hosted";
  if (state.scheduler.loaded) return "Running";
  if (state.scheduler.installed) return "Installed";
  return "Not installed";
}

function schedulerStatusDetail() {
  if (state.scheduler.hosted) {
    if (state.scheduler.active) return `Hosted scheduler is running now (${state.scheduler.timezone || "configured time"}).`;
    if (state.scheduler.last_error) return state.scheduler.last_error;
    if (state.scheduler.last_finished_at) return `Last hosted check finished at ${state.scheduler.last_finished_at}.`;
    if (state.scheduler.started_at) return `Hosted scheduler started at ${state.scheduler.started_at}.`;
    return "Hosted scheduler is enabled; waiting for its first heartbeat.";
  }
  if (state.scheduler.error) return state.scheduler.error;
  if (state.scheduler.loaded) return "Local background sender is running.";
  if (state.scheduler.installed) return "Local sender is installed but macOS has not loaded it.";
  return "Local background sender is not installed.";
}

function renderStorage() {
  const banner = $("#storageBanner");
  if (!banner) return;

  const storage = state.storage;
  if (!storage) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }

  const hasProblem = storage.warning || !storage.writable;
  if (!hasProblem) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }

  banner.hidden = false;
  banner.innerHTML = `
    <strong>Profile storage needs attention</strong>
    <p>${escapeHtml(storage.warning || storage.error || "Profile storage is not writable.")}</p>
    <div class="storage-facts">
      <span>Path: ${escapeHtml(storage.path || "")}</span>
      <span>${storage.writable ? "Writable" : "Not writable"}</span>
      <span>${storage.persistent ? "Persistent" : "Not confirmed persistent"}</span>
    </div>
  `;
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
    list.innerHTML = '<p class="quiet">No tracked sources yet.</p>';
    return;
  }

  list.innerHTML = sources
    .map((source) => {
      const sender = (source.senders || [])[0] || "";
      const pending = ["needs_subscription", "pending_subscription", "pending_confirmation", "manual_required"].includes(source.status);
      const statusClass = source.enabled === false ? "bad" : pending ? "warn" : "good";
      const statusLabels = {
        receiving: "Receiving",
        needs_subscription: "Needs subscription",
        pending_subscription: "Pending subscription",
        pending_confirmation: "Pending confirmation",
        manual_required: "Manual signup needed",
        failed_signup: "Signup failed",
      };
      const status = source.enabled === false ? "Tracking off" : statusLabels[source.status] || "Receiving";
      const link = source.subscription_url
        ? `<a href="${escapeAttr(source.subscription_url)}" data-source-status="${escapeAttr(sender)}" data-status="pending_subscription" target="_blank" rel="noreferrer">Open subscribe page</a>`
        : "";
      const subscriptionEmail = source.subscription_email || activeProfile()?.subscription_email || "";
      const sourceActions = pending
        ? `
          <div class="rec-actions">
            <button class="secondary" data-check-source="${escapeAttr(sender)}">Check Gmail</button>
            <button class="secondary" data-source-status="${escapeAttr(sender)}" data-status="receiving">Mark receiving</button>
          </div>
        `
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
            ${subscriptionEmail ? `<span>Subscribe with ${escapeHtml(subscriptionEmail)}</span>` : ""}
            ${link ? `<span>${link}</span>` : ""}
          </div>
          ${source.subscription_result?.reason ? `<p>${escapeHtml(source.subscription_result.reason)}</p>` : ""}
          ${source.reason ? `<p>${escapeHtml(source.reason)}</p>` : ""}
          ${sourceActions}
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
          <div class="rec-actions">
            <button data-add-rec="${index}" data-mode="subscribe">Try subscribe</button>
            <button class="secondary" data-add-rec="${index}" data-mode="track">Save for later</button>
          </div>
        </div>
        <p>${escapeHtml(rec.why_relevant || "")}</p>
        <div class="source-senders">${escapeHtml((rec.likely_senders || []).join(", "))}</div>
        <div class="meta">
          <span>${Math.round((rec.confidence || 0) * 100)}%</span>
          ${activeProfile()?.subscription_email ? `<span>Subscribe with ${escapeHtml(activeProfile().subscription_email)}</span>` : ""}
          ${rec.subscription_url ? `<span>${escapeHtml(rec.subscription_url)}</span>` : ""}
          ${(rec.topics || []).slice(0, 4).map((topic) => `<span>${escapeHtml(topic)}</span>`).join("")}
        </div>
      </article>
    `)
    .join("");
}

function scoreValue(scores, key) {
  const value = Number(scores?.[key] || 0);
  return Number.isFinite(value) ? value : 0;
}

function scorePill(label, value) {
  const normalized = Math.max(0, Math.min(10, Number(value) || 0));
  return `
    <div class="score-pill">
      <div>
        <span>${escapeHtml(label)}</span>
        <strong>${normalized.toFixed(1)}</strong>
      </div>
      <i style="--score-width:${normalized * 10}%"></i>
    </div>
  `;
}

function renderRankingSummary(rankings) {
  const summary = rankings?.summary || {};
  const reviewLink = $("#reviewLatestDigest");

  if (rankings?.review_url) {
    reviewLink.href = rankings.review_url;
    reviewLink.hidden = false;
  } else {
    reviewLink.hidden = true;
  }

  if (rankings?.status === "no_newsletters") {
    $("#rankingSummary").innerHTML = `
      <article class="metric wide"><span>Status</span><strong>No new signal</strong></article>
      <article class="metric wide"><span>Digest</span><strong>${escapeHtml(summary.digest_id || "Empty digest")}</strong></article>
      <article class="metric wide"><span>Profile</span><strong>Checked</strong></article>
    `;
    $("#rankingHint").textContent = rankings?.message || "The latest run sent an empty digest because no approved newsletter emails were found.";
    $("#rankingCount").textContent = "0";
    return;
  }

  if (rankings?.status !== "ready") {
    $("#rankingSummary").innerHTML = `
      <article class="metric wide"><span>Status</span><strong>No rankings yet</strong></article>
      <article class="metric wide"><span>Next move</span><strong>Send a digest now</strong></article>
      <article class="metric wide"><span>Profile</span><strong>${activeProfile() ? "Ready" : "Missing"}</strong></article>
    `;
    $("#rankingHint").textContent = rankings?.message || "Send one digest to create the first ranked list.";
    $("#rankingCount").textContent = "0";
    return;
  }

  $("#rankingSummary").innerHTML = `
    <article class="metric"><span>Ranked</span><strong>${summary.total_ranked || 0}</strong></article>
    <article class="metric"><span>Sent</span><strong>${summary.sent_in_digest || 0}</strong></article>
    <article class="metric"><span>Avg score</span><strong>${Number(summary.average_score || 0).toFixed(1)}</strong></article>
    <article class="metric"><span>Top source</span><strong>${escapeHtml(summary.top_source || "None")}</strong></article>
  `;
  $("#rankingHint").textContent = summary.created_at
    ? `Latest digest: ${summary.digest_id || "profile digest"}`
    : "Latest digest rankings are loaded.";
}

function filteredRankingItems() {
  const items = state.rankings?.items || [];
  if (state.rankingFilter === "sent") {
    return items.filter((item) => item.include_in_digest);
  }
  if (state.rankingFilter === "held") {
    return items.filter((item) => !item.include_in_digest);
  }
  return items;
}

function renderLearningProfile(rankings) {
  const learned = rankings?.learned_preferences || {};
  const topics = Object.entries(learned.topic_weights || {})
    .sort((a, b) => Math.abs(Number(b[1] || 0)) - Math.abs(Number(a[1] || 0)))
    .slice(0, 8);
  const sources = Object.entries(learned.source_weights || {})
    .sort((a, b) => Math.abs(Number(b[1] || 0)) - Math.abs(Number(a[1] || 0)))
    .slice(0, 8);

  const group = (title, rows) => `
    <div class="learning-group">
      <h3>${title}</h3>
      ${
        rows.length
          ? rows.map(([name, value]) => `
              <div class="learning-row">
                <span>${escapeHtml(name)}</span>
                <strong>${Number(value || 0).toFixed(2)}</strong>
              </div>
            `).join("")
          : '<p class="quiet">No feedback learned yet.</p>'
      }
    </div>
  `;

  $("#learningList").innerHTML = `
    ${group("Topic weights", topics)}
    ${group("Source weights", sources)}
    <div class="learning-note">Use the feedback page from a digest email to move these weights over time.</div>
  `;
}

function renderRankings(rankings) {
  state.rankings = rankings;
  renderRankingSummary(rankings);
  renderLearningProfile(rankings);

  if (rankings?.status === "no_newsletters") {
    $("#rankingList").innerHTML = `
      <div class="empty-state">
        <h3>No new signal in the latest run.</h3>
        <p>${escapeHtml(rankings?.message || "Finn-Signal checked the approved sources and did not find matching newsletter emails.")}</p>
      </div>
    `;
    return;
  }

  if (rankings?.status !== "ready") {
    $("#rankingList").innerHTML = `
      <div class="empty-state">
        <h3>No ranked digest yet.</h3>
        <p>Use “Send digest now” on this tab or in Runs. After the first digest, this page will show every article, score, and reason.</p>
      </div>
    `;
    return;
  }

  const items = filteredRankingItems();
  $("#rankingCount").textContent = String(items.length);
  if (!items.length) {
    $("#rankingList").innerHTML = '<p class="quiet">No articles match this filter.</p>';
    return;
  }

  $("#rankingList").innerHTML = items
    .map((item) => {
      const scores = item.scores || {};
      const finalScore = scoreValue(scores, "final_score");
      const multiplier = scoreValue(scores, "learned_multiplier") || 1;
      const status = item.include_in_digest ? "Sent in digest" : "Held back";
      const statusClass = item.include_in_digest ? "good" : "warn";
      const rankLabel = item.item_number ? `#${item.item_number} in email` : `Rank ${item.rank || "?"}`;
      const link = item.url
        ? `<a href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer">Read full article</a>`
        : '<span>No source link captured</span>';

      return `
        <article class="ranking-card ${statusClass}">
          <div class="rank-badge">
            <span>Rank</span>
            <strong>${escapeHtml(item.rank || "?")}</strong>
          </div>
          <div class="ranking-main">
            <div class="ranking-title-row">
              <div>
                <div class="meta">
                  <span>${escapeHtml(status)}</span>
                  <span>${escapeHtml(rankLabel)}</span>
                  <span>${escapeHtml(item.source || "Unknown source")}</span>
                </div>
                <h3>${escapeHtml(item.title)}</h3>
              </div>
              <div class="final-score">
                <span>Score</span>
                <strong>${finalScore.toFixed(1)}</strong>
              </div>
            </div>
            <p>${escapeHtml(item.summary || "")}</p>
            <div class="score-grid">
              ${scorePill("Finn", scoreValue(scores, "finn_relevance"))}
              ${scorePill("World", scoreValue(scores, "global_importance"))}
              ${scorePill("Novelty", scoreValue(scores, "novelty"))}
              ${scorePill("Action", scoreValue(scores, "actionability"))}
            </div>
            <div class="reason-grid">
              <div>
                <span>Why for you</span>
                <p>${escapeHtml(item.why_finn_cares || "No explanation captured.")}</p>
              </div>
              <div>
                <span>Why broadly</span>
                <p>${escapeHtml(item.why_world_cares || "No explanation captured.")}</p>
              </div>
            </div>
            <div class="ranking-foot">
              <div class="meta">
                ${(item.topic_tags || []).slice(0, 5).map((topic) => `<span>${escapeHtml(topic)}</span>`).join("")}
                <span>${multiplier.toFixed(2)}x learned</span>
              </div>
              <div class="meta">${link}</div>
            </div>
            ${item.ranking_note ? `<p class="ranking-note">${escapeHtml(item.ranking_note)}</p>` : ""}
          </div>
        </article>
      `;
    })
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
  const selectedBeforeRefresh = $("#profileSelect")?.value || state.activeProfileId || window.localStorage.getItem(ACTIVE_PROFILE_STORAGE_KEY);
  const data = await api("/api/state");
  state.profiles = data.profiles || [];
  state.scheduler = data.scheduler || state.scheduler;
  state.storage = data.storage || null;
  if (selectedBeforeRefresh && state.profiles.some((profile) => profile.id === selectedBeforeRefresh)) {
    state.activeProfileId = selectedBeforeRefresh;
  }
  renderProfiles();
  renderStatus();
  renderStorage();
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

async function loadRankings() {
  const profile = activeProfile();
  if (!profile) {
    renderRankings({
      status: "empty",
      message: "Create or select a profile first.",
      items: [],
      learned_preferences: {},
    });
    return;
  }

  const rankings = await api(`/api/profiles/${encodeURIComponent(profile.id)}/rankings`);
  renderRankings(rankings);
}

async function sendDigestNow(outputSelector = "#runOutput") {
  const profile = requireProfile();
  const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/send-test`, { method: "POST", body: "{}" });
  if (outputSelector) {
    showResult(outputSelector, data.result);
  }
  await loadState();
  return data;
}

function showResult(selector, value) {
  $(selector).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function activeTabId() {
  return $(".view.is-active")?.id || "onboarding";
}

function pageGuideState() {
  return {
    active_tab: activeTabId(),
    profile: activeProfile(),
    visible_counts: {
      candidates: state.candidates.length,
      recommendations: state.recommendations.length,
      rankings: state.rankings?.items?.length || 0,
      sources: activeProfile()?.source_count || 0,
      pending_sources: activeProfile()?.pending_source_count || 0,
    },
  };
}

function openGuide() {
  $("#guidePanel").hidden = false;
  $("#guideToggle").setAttribute("aria-expanded", "true");
  $("#guideQuestion").focus();
}

function closeGuide() {
  $("#guidePanel").hidden = true;
  $("#guideToggle").setAttribute("aria-expanded", "false");
}

function clearGuideHighlights() {
  $$(".guide-highlight").forEach((element) => element.classList.remove("guide-highlight"));
}

function applyGuideHighlights(selectors) {
  clearGuideHighlights();
  const elements = (selectors || [])
    .map((selector) => {
      try {
        return document.querySelector(selector);
      } catch {
        return null;
      }
    })
    .filter(Boolean);

  elements.forEach((element) => element.classList.add("guide-highlight"));
  if (elements[0]) {
    elements[0].scrollIntoView({ behavior: "smooth", block: "center" });
  }
  window.setTimeout(clearGuideHighlights, 9000);
}

async function askGuide(question) {
  const answer = $("#guideAnswer");
  answer.textContent = "Thinking...";
  const data = await api("/api/site-guide/chat", {
    method: "POST",
    body: JSON.stringify({
      question,
      ...pageGuideState(),
    }),
  });
  answer.textContent = data.answer || "I could not find that.";
  applyGuideHighlights(data.highlights || []);
}

function wireTabs() {
  $$(".tab").forEach((button) => {
    button.addEventListener("click", async () => {
      $$(".tab").forEach((tab) => tab.classList.remove("is-active"));
      $$(".view").forEach((view) => view.classList.remove("is-active"));
      button.classList.add("is-active");
      $(`#${button.dataset.tab}`).classList.add("is-active");
      if (button.dataset.tab === "rankings") {
        try {
          await loadRankings();
        } catch (error) {
          renderRankings({ status: "empty", message: error.message, items: [], learned_preferences: {} });
        }
      }
    });
  });
}

function wireForms() {
  $("#profileSelect").addEventListener("change", async (event) => {
    state.activeProfileId = event.target.value;
    window.localStorage.setItem(ACTIVE_PROFILE_STORAGE_KEY, state.activeProfileId);
    state.candidates = [];
    state.recommendations = [];
    renderCandidates([]);
    renderRecommendations([]);
    renderProfileForm();
    renderStatus();
    await loadSources();
    if (activeTabId() === "rankings") {
      await loadRankings();
    }
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
      window.localStorage.setItem(ACTIVE_PROFILE_STORAGE_KEY, state.activeProfileId);
      await loadState();
      showResult("#profileResult", "Profile saved.");
    } catch (error) {
      showResult("#profileResult", error.message);
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
    const checkButton = event.target.closest("[data-check-source]");
    if (checkButton) {
      const done = setBusy(checkButton, "Checking...");
      try {
        const profile = requireProfile();
        const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/sources/check`, {
          method: "POST",
          body: JSON.stringify({ sender: checkButton.dataset.checkSource, days: 30 }),
        });
        renderSources(data.sources || []);
        await loadState();
      } catch (error) {
        alert(error.message);
      } finally {
        done();
      }
      return;
    }

    const statusControl = event.target.closest("[data-source-status]");
    if (statusControl) {
      if (statusControl.tagName === "A") {
        event.preventDefault();
      }
      const done = setBusy(statusControl, statusControl.dataset.status === "receiving" ? "Saving..." : "Tracking...");
      try {
        const profile = requireProfile();
        const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/sources/status`, {
          method: "POST",
          body: JSON.stringify({
            sender: statusControl.dataset.sourceStatus,
            status: statusControl.dataset.status,
          }),
        });
        renderSources(data.sources || []);
        await loadState();
        if (statusControl.tagName === "A" && statusControl.href) {
          window.open(statusControl.href, "_blank", "noopener,noreferrer");
        }
      } catch (error) {
        alert(error.message);
      } finally {
        done();
      }
      return;
    }

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
    const done = setBusy(button, button.dataset.mode === "subscribe" ? "Trying..." : "Saving...");
    try {
      const profile = requireProfile();
      const recommendation = state.recommendations[Number(button.dataset.addRec)];
      const data = await api(`/api/profiles/${encodeURIComponent(profile.id)}/recommendations/add`, {
        method: "POST",
        body: JSON.stringify({ recommendation, mode: button.dataset.mode || "track" }),
      });
      renderSources(data.sources || []);
      await loadState();
      if (data.subscription?.status === "manual_required" && data.source?.subscription_url) {
        window.open(data.source.subscription_url, "_blank", "noopener,noreferrer");
      }
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
      state.scheduler = data.scheduler || state.scheduler;
      renderStatus();
      const label = schedulerStatusLabel();
      const detail = schedulerStatusDetail();
      showResult("#scheduleResult", `Schedule saved. Runner: ${label}. ${detail}`);
    } catch (error) {
      showResult("#scheduleResult", error.message);
    } finally {
      done();
    }
  });

  $("#refreshRankings").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Refreshing...");
    try {
      await loadRankings();
    } catch (error) {
      renderRankings({ status: "empty", message: error.message, items: [], learned_preferences: {} });
    } finally {
      done();
    }
  });

  $("#rankingSendTest").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Starting...");
    try {
      $("#rankingHint").textContent = "Starting a digest send. This can take several minutes.";
      const data = await sendDigestNow(null);
      $("#rankingHint").textContent = data.result?.message || "Digest send started. Refresh status to see completion.";
      await loadRankings();
    } catch (error) {
      $("#rankingHint").textContent = error.message;
    } finally {
      done();
    }
  });

  $$("[data-ranking-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.rankingFilter = button.dataset.rankingFilter;
      $$("[data-ranking-filter]").forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      renderRankings(state.rankings || { status: "empty", items: [], learned_preferences: {} });
    });
  });

  $("#sendTest").addEventListener("click", async (event) => {
    const done = setBusy(event.currentTarget, "Starting...");
    try {
      await sendDigestNow("#runOutput");
    } catch (error) {
      showResult("#runOutput", error.message);
    } finally {
      done();
    }
  });

  $("#guideToggle").addEventListener("click", () => {
    if ($("#guidePanel").hidden) {
      openGuide();
    } else {
      closeGuide();
    }
  });

  $("#guideClose").addEventListener("click", closeGuide);

  $$("[data-guide-question]").forEach((button) => {
    button.addEventListener("click", async () => {
      openGuide();
      $("#guideQuestion").value = button.dataset.guideQuestion;
      try {
        await askGuide(button.dataset.guideQuestion);
      } catch (error) {
        $("#guideAnswer").textContent = error.message;
      }
    });
  });

  $("#guideAsk").addEventListener("click", async () => {
    const question = $("#guideQuestion").value.trim();
    if (!question) {
      $("#guideAnswer").textContent = "Ask what you want to do in the app.";
      return;
    }
    try {
      await askGuide(question);
    } catch (error) {
      $("#guideAnswer").textContent = error.message;
    }
  });
}

wireTabs();
wireForms();
loadState().catch((error) => {
  $("#statusStrip").innerHTML = `<div class="metric"><span>Error</span><strong>${escapeHtml(error.message)}</strong></div>`;
});
