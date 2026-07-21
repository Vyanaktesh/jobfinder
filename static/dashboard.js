(() => {
  const search = document.getElementById('searchBox');
  const laneFilter = document.getElementById('laneFilter');
  const sponsorFilter = document.getElementById('sponsorFilter');
  const platformFilter = document.getElementById('platformFilter');
  const aiFilter = document.getElementById('aiFilter');
  const newOnly = document.getElementById('newOnly');
  const programOnly = document.getElementById('programOnly');
  const hideApplied = document.getElementById('hideApplied');
  const statusBar = document.getElementById('statusBar');
  const tbody = document.getElementById('jobBody');
  const headers = document.querySelectorAll('th.sortable');
  const typeTabs = document.querySelectorAll('.type-tab');

  let sortCol = 'eval_global_score';
  let sortAsc = false;
  let typeFilter = ''; // '' = all, 'fulltime', 'internship'

  // --- Finding #20: Persist filter state across page reloads ---
  const FILTERS_KEY = 'jobscraper_filters';

  function saveFilterState() {
    const state = {
      search: search.value,
      lane: laneFilter.value,
      sponsor: sponsorFilter.value,
      platform: platformFilter.value,
      ai: aiFilter.value,
      newOnly: newOnly.checked,
      programOnly: programOnly.checked,
      hideApplied: hideApplied.checked,
      typeFilter,
      sortCol,
      sortAsc,
    };
    try { localStorage.setItem(FILTERS_KEY, JSON.stringify(state)); } catch {}
  }

  function restoreFilterState() {
    try {
      const state = JSON.parse(localStorage.getItem(FILTERS_KEY));
      if (!state) return;
      search.value = state.search || '';
      laneFilter.value = state.lane || '';
      sponsorFilter.value = state.sponsor || '';
      platformFilter.value = state.platform || '';
      aiFilter.value = state.ai || '';
      newOnly.checked = !!state.newOnly;
      programOnly.checked = !!state.programOnly;
      hideApplied.checked = !!state.hideApplied;
      typeFilter = state.typeFilter || '';
      setActiveTab(typeFilter);
      if (state.sortCol) sortCol = state.sortCol;
      if (state.sortAsc !== undefined) sortAsc = state.sortAsc;
    } catch {}
  }

  function setActiveTab(type) {
    typeFilter = type;
    typeTabs.forEach(t => t.classList.toggle('active', t.dataset.type === type));
  }

  function applyFilters() {
    const q = search.value.toLowerCase().trim();
    const lane = laneFilter.value;
    const sponsor = sponsorFilter.value;
    const platform = platformFilter.value;
    const ai = aiFilter.value;
    const onlyNew = newOnly.checked;
    const onlyProgram = programOnly.checked;
    const noApplied = hideApplied.checked;
    const visited = getVisited();

    let shown = 0;
    const rows = tbody.querySelectorAll('tr.job-row');

    rows.forEach(row => {
      let visible = true;

      if (q) {
        const text = row.dataset.company + ' ' + row.dataset.title + ' ' + row.dataset.location;
        if (!text.includes(q)) visible = false;
      }

      if (lane && row.dataset.lane !== lane) visible = false;
      if (sponsor && row.dataset.sponsor !== sponsor) visible = false;
      if (platform && row.dataset.platform !== platform) visible = false;
      if (onlyNew && row.dataset.new !== '1') visible = false;
      if (onlyProgram && row.dataset.rotational !== '1') visible = false;

      // Employment type filter — internships have their own dedicated tab so
      // they're never silently mixed into (or hidden from) full-time results.
      if (typeFilter === 'internship' && row.dataset.internship !== '1') visible = false;
      if (typeFilter === 'fulltime' && row.dataset.internship === '1') visible = false;

      // AI score filter
      if (ai) {
        const action = row.dataset.evalaction || '';
        if (ai === 'unevaluated') {
          if (action !== '') visible = false;
        } else {
          if (action !== ai) visible = false;
        }
      }

      if (noApplied) {
        const link = row.querySelector('a.apply-btn');
        if (link && visited[link.href]) visible = false;
      }

      row.classList.toggle('hidden', !visible);
      if (visible) shown++;
    });

    const label = typeFilter === 'internship' ? 'internships' : typeFilter === 'fulltime' ? 'full-time jobs' : 'jobs';
    statusBar.textContent = `Showing ${shown} of ${rows.length} ${label}`;
    saveFilterState();
  }

  function sortTable(col) {
    if (sortCol === col) {
      sortAsc = !sortAsc;
    } else {
      sortCol = col;
      sortAsc = true;
    }

    headers.forEach(h => {
      h.classList.remove('sort-active', 'sort-desc');
      if (h.dataset.sort === col) {
        h.classList.add('sort-active');
        if (!sortAsc) h.classList.add('sort-desc');
      }
    });

    const rows = Array.from(tbody.querySelectorAll('tr.job-row'));
    rows.sort((a, b) => {
      const keyMap = {
        'company_name': 'company',
        'matched_lane': 'lane',
        'location_parsed': 'location',
        'posted_at': 'posted',
        'sponsorship_flag': 'sponsor',
        'match_score': 'score',
        'eval_global_score': 'evalscore',
      };
      const key = keyMap[col] || col;
      let va = a.dataset[key] || '';
      let vb = b.dataset[key] || '';

      if (col === 'match_score' || col === 'eval_global_score') {
        va = parseFloat(va) || 0;
        vb = parseFloat(vb) || 0;
        return sortAsc ? va - vb : vb - va;
      }

      if (col === 'posted_at') {
        va = va || '0000';
        vb = vb || '0000';
      }

      const cmp = va.localeCompare(vb);
      return sortAsc ? cmp : -cmp;
    });

    rows.forEach(r => tbody.appendChild(r));
    saveFilterState();
  }

  // --- Visited link tracking via localStorage ---
  const VISITED_KEY = 'jobscraper_visited';

  function getVisited() {
    try { return JSON.parse(localStorage.getItem(VISITED_KEY)) || {}; } catch { return {}; }
  }

  function markVisited(url) {
    const v = getVisited();
    v[url] = Date.now();
    localStorage.setItem(VISITED_KEY, JSON.stringify(v));
  }

  function applyVisitedStyles() {
    const v = getVisited();
    document.querySelectorAll('a.apply-btn').forEach(a => {
      if (v[a.href]) {
        a.classList.add('visited');
        a.textContent = 'Applied ✓';
        a.closest('tr.job-row')?.classList.add('visited-row');
      }
    });
  }

  // Mark link as visited on click
  document.addEventListener('click', e => {
    const btn = e.target.closest('a.apply-btn');
    if (btn) {
      markVisited(btn.href);
      btn.classList.add('visited');
      btn.textContent = 'Applied ✓';
      btn.closest('tr.job-row')?.classList.add('visited-row');
    }
  });

  // Apply on page load
  applyVisitedStyles();
  restoreFilterState();

  // Event listeners
  search.addEventListener('input', applyFilters);
  laneFilter.addEventListener('change', applyFilters);
  sponsorFilter.addEventListener('change', applyFilters);
  platformFilter.addEventListener('change', applyFilters);
  aiFilter.addEventListener('change', applyFilters);
  newOnly.addEventListener('change', applyFilters);
  programOnly.addEventListener('change', applyFilters);
  hideApplied.addEventListener('change', applyFilters);

  typeTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      setActiveTab(tab.dataset.type);
      applyFilters();
    });
  });

  headers.forEach(h => {
    h.addEventListener('click', () => {
      sortTable(h.dataset.sort);
    });
  });

  // --- Feedback system ---
  const FEEDBACK_KEY = 'jobscraper_feedback';

  function getFeedback() {
    try { return JSON.parse(localStorage.getItem(FEEDBACK_KEY)) || {}; } catch { return {}; }
  }

  function saveFeedback(fb) {
    localStorage.setItem(FEEDBACK_KEY, JSON.stringify(fb));
    updateFeedbackBar();
  }

  function updateFeedbackBar() {
    const fb = getFeedback();
    const count = Object.keys(fb).length;
    const countEl = document.getElementById('feedbackCount');
    const exportBtn = document.getElementById('exportFeedback');
    const clearBtn = document.getElementById('clearFeedback');

    countEl.textContent = count === 0 ? '0 pending feedback' : `${count} pending feedback`;
    countEl.classList.toggle('has-feedback', count > 0);
    exportBtn.disabled = count === 0;
    clearBtn.disabled = count === 0;
  }

  function restoreFeedbackUI() {
    const fb = getFeedback();
    Object.entries(fb).forEach(([jobId, entry]) => {
      const btns = document.querySelector(`.feedback-btns[data-jobid="${jobId}"]`);
      if (!btns) return;
      const btn = btns.querySelector(`[data-type="${entry.type}"]`);
      if (btn) btn.classList.add('fb-active');
      const status = document.querySelector(`.fb-status[data-jobid="${jobId}"]`);
      if (status) {
        const labels = { confirmed_good: 'Good', regret_applied: 'Regret', regret_skipped: 'Missed' };
        status.textContent = labels[entry.type] || '';
      }
    });
    updateFeedbackBar();
  }

  // Handle feedback button clicks
  document.addEventListener('click', e => {
    const btn = e.target.closest('.fb-btn');
    if (!btn) return;

    const container = btn.closest('.feedback-btns');
    const jobId = container.dataset.jobid;
    const type = btn.dataset.type;
    const fb = getFeedback();

    // Toggle: clicking same button again removes feedback
    if (fb[jobId] && fb[jobId].type === type) {
      delete fb[jobId];
      container.querySelectorAll('.fb-btn').forEach(b => b.classList.remove('fb-active'));
      const status = document.querySelector(`.fb-status[data-jobid="${jobId}"]`);
      if (status) status.textContent = '';
    } else {
      fb[jobId] = { type, timestamp: new Date().toISOString() };
      container.querySelectorAll('.fb-btn').forEach(b => b.classList.remove('fb-active'));
      btn.classList.add('fb-active');
      const labels = { confirmed_good: 'Good', regret_applied: 'Regret', regret_skipped: 'Missed' };
      const status = document.querySelector(`.fb-status[data-jobid="${jobId}"]`);
      if (status) status.textContent = labels[type] || '';
    }

    saveFeedback(fb);
  });

  // Export feedback as JSON download
  document.getElementById('exportFeedback').addEventListener('click', () => {
    const fb = getFeedback();
    if (Object.keys(fb).length === 0) return;

    const exportData = Object.entries(fb).map(([jobId, entry]) => ({
      job_id: parseInt(jobId, 10),
      feedback_type: entry.type,
      timestamp: entry.timestamp,
    }));

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'feedback.json';
    a.click();
    URL.revokeObjectURL(url);
  });

  // Clear all feedback
  document.getElementById('clearFeedback').addEventListener('click', () => {
    if (!confirm('Clear all pending feedback?')) return;
    localStorage.removeItem(FEEDBACK_KEY);
    document.querySelectorAll('.fb-btn').forEach(b => b.classList.remove('fb-active'));
    document.querySelectorAll('.fb-status').forEach(s => s.textContent = '');
    updateFeedbackBar();
  });

  restoreFeedbackUI();

  // --- Autofill copy-to-clipboard ---
  function showToast(msg, duration) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), duration || 3000);
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('.autofill-btn');
    if (!btn) return;
    if (typeof AUTOFILL_JS === 'undefined' || !AUTOFILL_JS) {
      showToast('No autofill script found — run: python -m src.apply.bookmarklet', 4000);
      return;
    }
    navigator.clipboard.writeText(AUTOFILL_JS).then(() => {
      showToast('Autofill copied! Open console (Cmd+Option+J) and paste.', 3500);
    }).catch(() => {
      showToast('Copy failed — open console and paste manually.', 3000);
    });
  });

  // --- Control server integration (Run Pipeline + Clean Resumes buttons) ---
  // A file:// dashboard can't spawn processes; these buttons call a small local
  // server (scripts/control_server.py) over CORS. Buttons degrade gracefully
  // when the server isn't running.
  const CONTROL_BASE = (localStorage.getItem('jobscraper_control_base')
    || 'http://localhost:8765').replace(/\/$/, '');
  const runBtn = document.getElementById('runPipelineBtn');
  const cleanupBtn = document.getElementById('cleanupBtn');
  const controlStatus = document.getElementById('controlStatus');
  const runProgress = document.getElementById('runProgress');
  let serverUp = false;

  // Tidy the raw log line into something readable for the toolbar.
  function formatProgress(p) {
    if (!p) return '';
    // "[ashby] Progress: 1400/3179 companies scraped" -> "Ashby 1400/3179"
    let m = p.match(/\[(\w+)\]\s*Progress:\s*([\d]+\/[\d]+)/i);
    if (m) return m[1].charAt(0).toUpperCase() + m[1].slice(1) + ' ' + m[2];
    m = p.match(/\[(\w+)\]\s*Done:/i);
    if (m) return m[1].charAt(0).toUpperCase() + m[1].slice(1) + ' done';
    if (/AI evaluating/i.test(p)) return 'AI evaluating…';
    if (/AI evaluation complete/i.test(p)) return 'AI eval done';
    if (/Passed filters/i.test(p)) return 'Filtering…';
    if (/Dashboard:/i.test(p)) return 'Writing dashboard…';
    if (/Scraping (\w+)/i.test(p)) return 'Scraping ' + RegExp.$1;
    return p.slice(0, 60);
  }

  function setControlStatus(up, detail) {
    serverUp = up;
    if (!controlStatus) return;
    if (up) {
      controlStatus.innerHTML = '● connected';
      controlStatus.className = 'control-status up';
      controlStatus.title = detail || 'Control server connected';
    } else {
      controlStatus.innerHTML = '● offline';
      controlStatus.className = 'control-status down';
      controlStatus.title = 'Start it: python scripts/control_server.py';
    }
    if (runBtn) runBtn.disabled = !up;
    if (cleanupBtn) cleanupBtn.disabled = !up;
  }

  async function pingControl() {
    try {
      const r = await fetch(CONTROL_BASE + '/status', { method: 'GET' });
      if (!r.ok) throw new Error('bad status');
      const s = await r.json();
      let detail = s.running ? 'Pipeline RUNNING' : 'Idle';
      if (s.progress) detail += ' — ' + s.progress;
      if (s.apply_count != null) detail += ` (${s.apply_count} apply)`;
      setControlStatus(true, detail);
      if (runBtn) {
        runBtn.textContent = s.running ? '⏳ Running…' : '▶ Run Pipeline';
        runBtn.disabled = s.running;
      }
      if (runProgress) {
        if (s.running) {
          const label = formatProgress(s.progress);
          runProgress.textContent = label ? '↻ ' + label : '↻ working…';
          runProgress.classList.add('active');
        } else {
          runProgress.textContent = '';
          runProgress.classList.remove('active');
        }
      }
    } catch {
      setControlStatus(false);
      if (runProgress) { runProgress.textContent = ''; runProgress.classList.remove('active'); }
    }
  }

  // Collect job IDs of rows the user has marked applied (visited apply link)
  function appliedJobIds() {
    const visited = getVisited();
    const ids = [];
    document.querySelectorAll('tr.job-row').forEach(row => {
      const link = row.querySelector('a.apply-btn');
      if (link && visited[link.href] && row.dataset.jobid) {
        ids.push(parseInt(row.dataset.jobid, 10));
      }
    });
    return ids;
  }

  if (runBtn) {
    runBtn.addEventListener('click', async () => {
      if (!serverUp) { showToast('Control server offline. Run: python scripts/control_server.py', 4000); return; }
      runBtn.disabled = true;
      try {
        const r = await fetch(CONTROL_BASE + '/run', { method: 'POST' });
        const d = await r.json();
        showToast(d.message || (d.ok ? 'Pipeline started' : 'Could not start'), 4000);
      } catch {
        showToast('Failed to reach control server.', 3000);
      }
      setTimeout(pingControl, 1500);
    });
  }

  if (cleanupBtn) {
    cleanupBtn.addEventListener('click', async () => {
      if (!serverUp) { showToast('Control server offline. Run: python scripts/control_server.py', 4000); return; }
      const ids = appliedJobIds();
      if (ids.length === 0) {
        showToast('No applied jobs yet. Click an Apply link first to mark it applied.', 4000);
        return;
      }
      if (!confirm(`Delete tailored resumes for ${ids.length} applied job(s)?`)) return;
      try {
        const r = await fetch(CONTROL_BASE + '/cleanup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_ids: ids }),
        });
        const d = await r.json();
        showToast(d.ok ? `Deleted ${d.deleted} resume PDF(s).` : (d.message || 'Cleanup failed'), 4000);
      } catch {
        showToast('Failed to reach control server.', 3000);
      }
    });
  }

  // Poll the control server: once now, then every 10s.
  pingControl();
  setInterval(pingControl, 10000);

  // Initial sort + filter (uses restored state or defaults)
  sortTable(sortCol);
  applyFilters();
})();
