(() => {
  // localStorage can throw (file:// in some browsers, private mode). Never let that break the UI.
  const store = {
    get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
    del(k) { try { localStorage.removeItem(k); } catch {} },
  };
  const search = document.getElementById('searchBox');
  const laneFilter = document.getElementById('laneFilter');
  const sponsorFilter = document.getElementById('sponsorFilter');
  const platformFilter = document.getElementById('platformFilter');
  const newOnly = document.getElementById('newOnly');
  const programOnly = document.getElementById('programOnly');
  const hideApplied = document.getElementById('hideApplied');
  const postedFilter = document.getElementById('postedFilter');
  const h1bOnly = document.getElementById('h1bOnly');
  const emptyState = document.getElementById('emptyState');
  const statusBar = document.getElementById('statusBar');
  const tbody = document.getElementById('jobBody');
  const headers = document.querySelectorAll('th.sortable');
  const typeTabs = document.querySelectorAll('.type-tab');

  let sortCol = 'sponsorship_flag';
  let sortAsc = true;
  let typeFilter = ''; // '' = all, 'fulltime', 'internship', 'saved', 'applied'

  // --- Finding #20: Persist filter state across page reloads ---
  const FILTERS_KEY = 'jobscraper_filters';

  function saveFilterState() {
    const state = {
      search: search.value,
      lane: laneFilter.value,
      sponsor: sponsorFilter.value,
      platform: platformFilter.value,
      newOnly: newOnly.checked,
      programOnly: programOnly.checked,
      hideApplied: hideApplied.checked,
      posted: postedFilter.value,
      h1bOnly: h1bOnly.checked,
      typeFilter,
      sortCol,
      sortAsc,
    };
    store.set(FILTERS_KEY, state);
  }

  function restoreFilterState() {
    try {
      const state = store.get(FILTERS_KEY, null);
      if (!state) return;
      search.value = state.search || '';
      laneFilter.value = state.lane || '';
      sponsorFilter.value = state.sponsor || '';
      platformFilter.value = state.platform || '';
      newOnly.checked = !!state.newOnly;
      programOnly.checked = !!state.programOnly;
      hideApplied.checked = !!state.hideApplied;
      postedFilter.value = state.posted || '';
      h1bOnly.checked = !!state.h1bOnly;
      typeFilter = state.typeFilter || '';
      setActiveTab(typeFilter);
      if (state.sortCol && document.querySelector(`th.sortable[data-sort="${state.sortCol}"]`)) sortCol = state.sortCol;
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
    const onlyNew = newOnly.checked;
    const onlyProgram = programOnly.checked;
    const noApplied = hideApplied.checked;
    const maxAge = parseInt(postedFilter.value, 10) || 0;
    const onlyH1b = h1bOnly.checked;
    const visited = getVisited();
    const saved = getSaved();
    const cutoff = maxAge ? Date.now() - maxAge * 86400000 : 0;

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
      if (typeFilter === 'saved' && !saved[row.dataset.jobid]) visible = false;
      if (typeFilter === 'applied' && !isApplied(row, visited)) visible = false;

      if (onlyH1b && !(row.dataset.sponsor === 'GREEN' || row.dataset.sponsor === 'YELLOW' || row.dataset.sponsor === 'NA')) visible = false;
      if (cutoff) {
        const t = Date.parse(row.dataset.posted);
        if (!t || t < cutoff) visible = false;
      }

      if (noApplied && isApplied(row, visited)) visible = false;

      row.classList.toggle('hidden', !visible);
      if (visible) shown++;
    });

    const label = typeFilter === 'internship' ? 'internships' : typeFilter === 'fulltime' ? 'full-time jobs' : 'jobs';
    const appliedN = Array.from(rows).filter(r => isApplied(r, visited)).length;
    statusBar.textContent = `Showing ${shown} of ${rows.length} ${label} · ${appliedN} applied`;
    if (emptyState) emptyState.classList.toggle('show', shown === 0);
    updateTabCounts(rows, visited, saved);
    saveFilterState();
  }

  // Header click: toggle direction (or switch column). Restore/initial render
  // calls applySort() directly so reloading never flips the saved direction.
  function sortTable(col) {
    if (sortCol === col) {
      sortAsc = !sortAsc;
    } else {
      sortCol = col;
      sortAsc = !(col === 'match_score' || col === 'posted_at' || col === 'h1b_count');
    }
    applySort();
  }

  function applySort() {
    const col = sortCol;

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
        'h1b_count': 'h1b',
      };
      const key = keyMap[col] || col;
      let va = a.dataset[key] || '';
      let vb = b.dataset[key] || '';

      if (col === 'sponsorship_flag') {
        // GREEN, YELLOW, internship N/A, then RED; ties broken by H1B petition count (desc)
        const rank = { GREEN: 0, YELLOW: 1, NA: 2, RED: 3 };
        const d = (rank[va] ?? 3) - (rank[vb] ?? 3);
        const byCount = (parseFloat(b.dataset.h1b) || 0) - (parseFloat(a.dataset.h1b) || 0);
        return sortAsc ? (d || byCount) : -(d || byCount);
      }

      if (col === 'match_score') {
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

  function getVisited() { return store.get(VISITED_KEY, {}); }

  function markVisited(url) {
    const v = getVisited();
    v[url] = Date.now();
    store.set(VISITED_KEY, v);
  }

  function unmarkVisited(url) {
    const v = getVisited();
    delete v[url];
    store.set(VISITED_KEY, v);
  }

  function isApplied(row, visited) {
    const link = row.querySelector('a.apply-btn');
    return !!(link && (visited || getVisited())[link.href]);
  }

  function setRowApplied(row, applied) {
    const a = row.querySelector('a.apply-btn');
    if (!a) return;
    a.classList.toggle('visited', applied);
    a.textContent = applied ? 'Applied ✓' : 'Apply →';
    row.classList.toggle('visited-row', applied);
    const undo = row.querySelector('.undo-btn');
    if (undo) undo.hidden = !applied;
  }

  function applyVisitedStyles() {
    const v = getVisited();
    document.querySelectorAll('tr.job-row').forEach(row => setRowApplied(row, isApplied(row, v)));
  }

  // --- Saved (starred) jobs ---
  const SAVED_KEY = 'jobscraper_saved';
  function getSaved() { return store.get(SAVED_KEY, {}); }

  function applySavedStyles() {
    const sv = getSaved();
    document.querySelectorAll('tr.job-row').forEach(row => {
      const on = !!sv[row.dataset.jobid];
      const b = row.querySelector('.star-btn');
      if (b) { b.classList.toggle('on', on); b.textContent = on ? '\u2605' : '\u2606'; }
    });
  }

  function updateTabCounts(rows, visited, saved) {
    let intern = 0, applied = 0, savedN = 0;
    rows.forEach(r => {
      if (r.dataset.internship === '1') intern++;
      if (isApplied(r, visited)) applied++;
      if (saved[r.dataset.jobid]) savedN++;
    });
    const counts = { '': rows.length, fulltime: rows.length - intern, internship: intern, saved: savedN, applied };
    typeTabs.forEach(t => {
      const el = t.querySelector('.type-tab-count');
      if (el && counts[t.dataset.type] !== undefined) el.textContent = counts[t.dataset.type];
    });
  }

  function resetFilters() {
    search.value = ''; laneFilter.value = ''; sponsorFilter.value = ''; platformFilter.value = '';
    postedFilter.value = '';
    newOnly.checked = programOnly.checked = hideApplied.checked = false;
    h1bOnly.checked = false;
    setActiveTab('');
    applyFilters();
  }

  document.addEventListener('click', e => {
    const star = e.target.closest('.star-btn');
    if (star) {
      const id = star.closest('tr.job-row').dataset.jobid;
      const sv = getSaved();
      if (sv[id]) delete sv[id]; else sv[id] = Date.now();
      store.set(SAVED_KEY, sv);
      applySavedStyles();
      applyFilters();
      return;
    }
    const undo = e.target.closest('.undo-btn');
    if (undo) {
      const row = undo.closest('tr.job-row');
      unmarkVisited(row.querySelector('a.apply-btn').href);
      setRowApplied(row, false);
      applyFilters();
    }
  });

  document.getElementById('resetFilters')?.addEventListener('click', resetFilters);
  document.getElementById('emptyReset')?.addEventListener('click', resetFilters);

  // "/" focuses search (like GitHub/Gmail)
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) {
      e.preventDefault();
      search.focus();
    }
  });

  document.getElementById('exportApplied')?.addEventListener('click', () => {
    const v = getVisited();
    const rows = Array.from(document.querySelectorAll('tr.job-row')).filter(r => isApplied(r, v));
    if (!rows.length) { showToast('Nothing applied yet.', 3000); return; }
    const q = x => '"' + String(x == null ? '' : x).replace(/"/g, '""') + '"';
    const byId = Object.fromEntries(JOBS.map(j => [String(j.id), j]));
    const lines = ['company,title,location,h1b,applied_on,url'];
    rows.forEach(r => {
      const j = byId[r.dataset.jobid] || {};
      const url = r.querySelector('a.apply-btn').href;
      lines.push([j.company_name, j.title, j.location_parsed, j.sponsorship_flag,
        new Date(v[url]).toISOString().slice(0, 10), url].map(q).join(','));
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = 'applied_jobs.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  });

  // Mark link as applied on click
  document.addEventListener('click', e => {
    const btn = e.target.closest('a.apply-btn');
    if (btn) {
      markVisited(btn.href);
      setRowApplied(btn.closest('tr.job-row'), true);
      setTimeout(applyFilters, 0);
    }
  });

  // Apply on page load
  applyVisitedStyles();
  applySavedStyles();
  restoreFilterState();

  // Event listeners
  search.addEventListener('input', applyFilters);
  laneFilter.addEventListener('change', applyFilters);
  sponsorFilter.addEventListener('change', applyFilters);
  platformFilter.addEventListener('change', applyFilters);
  newOnly.addEventListener('change', applyFilters);
  programOnly.addEventListener('change', applyFilters);
  hideApplied.addEventListener('change', applyFilters);
  postedFilter.addEventListener('change', applyFilters);
  h1bOnly.addEventListener('change', applyFilters);

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
  const CONTROL_BASE = (store.get('jobscraper_control_base', 'http://localhost:8765')).replace(/\/$/, '');
  const runBtn = document.getElementById('runPipelineBtn');
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
  }

  async function pingControl() {
    try {
      const r = await fetch(CONTROL_BASE + '/status', { method: 'GET' });
      if (!r.ok) throw new Error('bad status');
      const s = await r.json();
      let detail = s.running ? 'Pipeline RUNNING' : 'Idle';
      if (s.progress) detail += ' — ' + s.progress;
      if (s.apply_count != null) detail += ` (${s.apply_count} H1B-friendly jobs)`;
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

  // Poll the control server: once now, then every 10s.
  pingControl();
  setInterval(pingControl, 10000);

  // Initial sort + filter (uses restored state or defaults)
  document.querySelectorAll('th.sortable').forEach(h => {
    h.classList.remove('sort-active', 'sort-desc');
    if (h.dataset.sort === sortCol) { h.classList.add('sort-active'); if (!sortAsc) h.classList.add('sort-desc'); }
  });
  applySort();
  applyFilters();
})();
