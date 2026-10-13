/* Job Alerts — filter interactions.
   No cookies, no storage, no network: filter state lives in the URL query.
   All report text is rendered server-side; this script only toggles
   visibility and writes text nodes (never untrusted HTML). */
(function () {
  "use strict";

  var dataEl = document.getElementById("site-data");
  var bar = document.getElementById("filter-bar");
  if (!dataEl || !bar) return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (err) {
    return;
  }
  if (!data || !data.page || !Array.isArray(data.jobs)) return;

  var scopeDate = data.page.scopeDate || "all";
  var STATUSES = ["all", "qualified", "rejected", "near-miss"];
  var MODES = ["all", "remote", "hybrid", "on-site", "unknown"];
  var EQUITY = ["all", "yes", "no", "unknown"];
  var SALARY = ["all", "eur-annual", "non-eur-annual", "numeric-unconfirmed", "unknown"];

  var searchInput = document.getElementById("filter-search");
  var dateSelect = document.getElementById("filter-date");
  var statusSelect = document.getElementById("filter-status");
  var modeSelect = document.getElementById("filter-mode");
  var equitySelect = document.getElementById("filter-equity");
  var salarySelect = document.getElementById("filter-salary");
  var resetButton = document.getElementById("filter-reset");
  var countEl = document.getElementById("result-count");
  var noResultsEl = document.getElementById("no-results");

  if (
    !searchInput || !dateSelect || !statusSelect || !modeSelect ||
    !equitySelect || !salarySelect || !resetButton || !countEl
  ) {
    return;
  }

  function defaults() {
    return { q: "", date: scopeDate, status: "all", mode: "all", equity: "all", salary: "all" };
  }

  function pick(list, value, fallback) {
    return list.indexOf(value) !== -1 ? value : fallback;
  }

  function fromUrl() {
    var state = defaults();
    var params = new URLSearchParams(window.location.search);
    if (params.has("q")) state.q = params.get("q") || "";
    if (params.has("date")) {
      var d = params.get("date");
      state.date = d === "all" || (data.dates || []).indexOf(d) !== -1 ? d : scopeDate;
    }
    if (params.has("status")) state.status = pick(STATUSES, params.get("status"), "all");
    if (params.has("mode")) state.mode = pick(MODES, params.get("mode"), "all");
    if (params.has("equity")) state.equity = pick(EQUITY, params.get("equity"), "all");
    if (params.has("salary")) state.salary = pick(SALARY, params.get("salary"), "all");
    return state;
  }

  function syncControls(state) {
    searchInput.value = state.q;
    dateSelect.value = state.date;
    statusSelect.value = state.status;
    modeSelect.value = state.mode;
    equitySelect.value = state.equity;
    salarySelect.value = state.salary;
  }

  function readControls() {
    return {
      q: searchInput.value.trim(),
      date: dateSelect.value,
      status: statusSelect.value,
      mode: modeSelect.value,
      equity: equitySelect.value,
      salary: salarySelect.value
    };
  }

  function writeUrl(state) {
    var base = defaults();
    var params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    if (state.date !== base.date) params.set("date", state.date);
    if (state.status !== "all") params.set("status", state.status);
    if (state.mode !== "all") params.set("mode", state.mode);
    if (state.equity !== "all") params.set("equity", state.equity);
    if (state.salary !== "all") params.set("salary", state.salary);
    var qs = params.toString();
    try {
      window.history.replaceState(
        null,
        "",
        window.location.pathname + (qs ? "?" + qs : "") + window.location.hash
      );
    } catch (err) {
      /* file:// or blocked history: filtering still works. */
    }
  }

  function matches(job, state) {
    if (state.date !== "all" && job.date !== state.date) return false;
    if (state.status !== "all" && job.status !== state.status) return false;
    if (state.mode !== "all" && job.mode !== state.mode) return false;
    if (state.equity !== "all" && job.equity !== state.equity) return false;
    if (state.salary !== "all" && job.salary !== state.salary) return false;
    if (state.q && (job.text || "").indexOf(state.q.toLowerCase()) === -1) return false;
    return true;
  }

  function cardFor(key) {
    return document.querySelector('.job-card[data-key="' + key + '"]');
  }

  function scopeCount(state, statuses) {
    var n = 0;
    for (var i = 0; i < data.jobs.length; i++) {
      var job = data.jobs[i];
      if (state.date !== "all" && job.date !== state.date) continue;
      if (statuses.indexOf(job.status) !== -1) n++;
    }
    return n;
  }

  function dataNoteText(kind, dateScope) {
    var scope = dateScope === "all" ? "any report" : "the " + dateScope + " report";
    if (kind === "qualified") {
      return (
        "No role passed every recorded gate in " + scope +
        ". The research leads below are not recommendations."
      );
    }
    return "No research leads recorded in " + scope + ".";
  }

  function sectionState(listId, dataNoteId, filterNoteId, statuses, state, visibleTotal, kind) {
    var list = document.getElementById(listId);
    var dataNote = document.getElementById(dataNoteId);
    var filterNote = document.getElementById(filterNoteId);
    if (!list) return;
    var scoped = scopeCount(state, statuses);
    if (scoped === 0) {
      if (dataNote) {
        dataNote.textContent = dataNoteText(kind, state.date);
        dataNote.hidden = false;
      }
      if (filterNote) filterNote.hidden = true;
    } else {
      if (dataNote) dataNote.hidden = true;
      if (filterNote) filterNote.hidden = visibleTotal !== 0;
    }
  }

  function apply(state) {
    var visible = 0;
    var visibleBySection = { qualified: 0, leads: 0 };
    for (var i = 0; i < data.jobs.length; i++) {
      var job = data.jobs[i];
      var card = cardFor(job.key);
      var ok = matches(job, state);
      if (card) card.hidden = !ok;
      if (ok) {
        visible++;
        if (job.status === "qualified") visibleBySection.qualified++;
        else visibleBySection.leads++;
      }
    }

    sectionState(
      "qualified-list", "qualified-empty-data", "qualified-empty-filter",
      ["qualified"], state, visibleBySection.qualified, "qualified"
    );
    sectionState(
      "leads-list", "leads-empty-data", "leads-empty-filter",
      ["rejected", "near-miss"], state, visibleBySection.leads, "leads"
    );

    countEl.textContent = "Showing " + visible + " of " + data.jobs.length + " entries";
    if (noResultsEl) noResultsEl.hidden = visible !== 0;
  }

  function refresh() {
    var state = readControls();
    apply(state);
    writeUrl(state);
  }

  function revealDeepLink() {
    if (!window.location.hash) return;
    var id = window.location.hash.slice(1);
    var target = document.getElementById(id);
    if (!target || !target.classList.contains("job-card")) return;
    if (!target.hidden) return;
    syncControls({ q: "", date: "all", status: "all", mode: "all", equity: "all", salary: "all" });
    apply(readControls());
    writeUrl(readControls());
    target.scrollIntoView();
  }

  searchInput.addEventListener("input", refresh);
  searchInput.addEventListener("search", refresh);
  dateSelect.addEventListener("change", refresh);
  statusSelect.addEventListener("change", refresh);
  modeSelect.addEventListener("change", refresh);
  equitySelect.addEventListener("change", refresh);
  salarySelect.addEventListener("change", refresh);
  resetButton.addEventListener("click", function () {
    syncControls(defaults());
    apply(defaults());
    writeUrl(defaults());
    searchInput.focus();
  });

  syncControls(fromUrl());
  apply(readControls());
  revealDeepLink();

  /* Keep controls honest if the user edits the query by hand. */
  window.addEventListener("popstate", function () {
    syncControls(fromUrl());
    apply(readControls());
  });
})();
