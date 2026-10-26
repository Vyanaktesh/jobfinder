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
    showTrackerView(type === 'tracker');
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
      if (typeFilter === 'saved' && statusOf(row) !== 'saved') visible = false;

      if (onlyH1b && !(row.dataset.sponsor === 'GREEN' || row.dataset.sponsor === 'YELLOW' || row.dataset.sponsor === 'NA')) visible = false;
      if (cutoff) {
        const t = Date.parse(row.dataset.posted);
        if (!t || t < cutoff) visible = false;
      }

      if (noApplied && isApplied(row)) visible = false;

      row.classList.toggle('hidden', !visible);
      if (visible) shown++;
    });

    const label = typeFilter === 'internship' ? 'internships' : typeFilter === 'fulltime' ? 'full-time jobs' : 'jobs';
    const appliedN = Object.values(apps).filter(a => TRACKED(a.status)).length;
    statusBar.textContent = `Showing ${shown} of ${rows.length} ${label} · ${appliedN} applied`;
    if (emptyState) emptyState.classList.toggle('show', shown === 0);
    updateTabCounts(rows);
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

  // =====================================================================
  // Application tracker
  // Source of truth is the local server's database (so it survives clearing the
  // browser); localStorage is a cache that also holds edits made while the
  // server was off, and they are pushed on the next connection.
  // =====================================================================
  const APPS_KEY = 'jobscraper_apps';
  const PENDING_KEY = 'jobscraper_apps_pending';
  const MIGRATED_KEY = 'jobscraper_apps_migrated';
  const VISITED_KEY = 'jobscraper_visited';   // legacy
  const SAVED_KEY = 'jobscraper_saved';       // legacy
  const STATUS_LABEL = { none: '—', saved: 'Saved', applied: 'Applied', interviewing: 'Interviewing',
                         offer: 'Offer', rejected: 'Rejected', withdrawn: 'Withdrawn' };
  const TRACKED = st => !!st && st !== 'none' && st !== 'saved';   // applied or later
  const FOLLOW_UP_DAYS = 7;
  const jobsByKey = Object.fromEntries((typeof JOBS !== 'undefined' ? JOBS : []).map(j => [j.dedup_key, j]));
  let apps = {};                 // job_key -> record
  let pending = new Set();       // job_keys changed but not yet saved on the server
  let trackerFilter = '';
  let trackerReady = false;
  let pushTimer = null;

  const nowIso = () => new Date().toISOString().slice(0, 19);
  const rowKey = row => row.dataset.key;
  const statusOf = row => (apps[rowKey(row)] || {}).status || 'none';
  const isApplied = row => TRACKED(statusOf(row));

  function mergeApp(rec) {
    if (!rec || !rec.job_key) return false;
    const cur = apps[rec.job_key];
    if (cur && (cur.updated_at || '') >= (rec.updated_at || '')) return false;
    apps[rec.job_key] = rec;
    return true;
  }

  function persistLocal() {
    store.set(APPS_KEY, apps);
    store.set(PENDING_KEY, Array.from(pending));
  }

  function snapshot(key) {
    const j = jobsByKey[key] || {};
    return { job_key: key, status: 'none', notes: '', applied_at: '', updated_at: '',
             company: j.company_name || '', title: j.title || '', location: j.location_parsed || '',
             apply_url: j.apply_url || '', sponsorship_flag: j.sponsorship_flag || '', h1b_count: j.h1b_count || 0 };
  }

  function initTracker() {
    apps = {};
    Object.values(store.get(APPS_KEY, {})).forEach(mergeApp);
    const embedded = {};
    (typeof APPLICATIONS !== 'undefined' ? APPLICATIONS : []).forEach(a => { embedded[a.job_key] = a; mergeApp(a); });

    // One-time import of the old browser-only "applied" / "saved" marks
    if (!store.get(MIGRATED_KEY, false)) {
      const visited = store.get(VISITED_KEY, {});
      const saved = store.get(SAVED_KEY, {});
      document.querySelectorAll('tr.job-row').forEach(row => {
        const key = rowKey(row);
        if ((apps[key] || {}).status && apps[key].status !== 'none') return;
        const link = row.querySelector('a.apply-btn');
        const ts = link && visited[link.href];
        if (ts) {
          const when = new Date(ts).toISOString().slice(0, 19);
          apps[key] = Object.assign(snapshot(key), { status: 'applied', applied_at: when, updated_at: when });
        } else if (saved[row.dataset.jobid]) {
          apps[key] = Object.assign(snapshot(key), { status: 'saved', updated_at: nowIso() });
        }
      });
      store.set(MIGRATED_KEY, true);
    }

    // Anything newer locally than what the page was built with must reach the server
    pending = new Set(store.get(PENDING_KEY, []));
    Object.keys(apps).forEach(k => {
      if (!embedded[k] || (embedded[k].updated_at || '') < (apps[k].updated_at || '')) pending.add(k);
    });
    persistLocal();
    trackerReady = true;
    refreshAllRows();
    if (typeFilter === 'tracker') renderTracker();
  }

  function refreshRow(row) {
    const key = rowKey(row);
    const st = (apps[key] || {}).status || 'none';
    const sel = row.querySelector('.status-select');
    if (sel && sel.value !== st) sel.value = st;
    const a = row.querySelector('a.apply-btn');
    const done = TRACKED(st);
    if (a) {
      a.classList.toggle('visited', done);
      a.textContent = done ? (STATUS_LABEL[st] + ' ✓') : 'Apply →';
    }
    row.classList.toggle('visited-row', done);
    row.dataset.status = st;
  }

  function refreshAllRows() {
    document.querySelectorAll('tr.job-row').forEach(refreshRow);
  }

  function setStatus(key, status) {
    const rec = Object.assign({}, apps[key] || snapshot(key));
    rec.status = status;
    rec.updated_at = nowIso();
    if (TRACKED(status) && !rec.applied_at) rec.applied_at = rec.updated_at;
    if (status === 'none' || status === 'saved') rec.applied_at = '';
    apps[key] = rec;
    changed(key);
  }

  function setNotes(key, notes) {
    const rec = apps[key];
    if (!rec || rec.notes === notes) return;
    rec.notes = notes;
    rec.updated_at = nowIso();
    changed(key);
  }

  function changed(key) {
    pending.add(key);
    persistLocal();
    document.querySelectorAll(`tr.job-row[data-key="${CSS.escape(key)}"]`).forEach(refreshRow);
    applyFilters();
    if (typeFilter === 'tracker') renderTracker();
    clearTimeout(pushTimer);
    pushTimer = setTimeout(pushPending, 400);
  }

  async function pushPending() {
    if (!pending.size || typeof serverUp === 'undefined' || !serverUp) return;
    const keys = Array.from(pending);
    const records = keys.map(k => apps[k]).filter(Boolean);
    const d = await post('/applications', { applications: records });
    if (d && d.ok) {
      keys.forEach(k => pending.delete(k));
      persistLocal();
    }
  }

  async function syncFromServer() {
    try {
      const r = await fetch(CONTROL_BASE + '/applications', { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      let any = false;
      (d.applications || []).forEach(a => { if (mergeApp(a)) any = true; });
      if (any) {
        persistLocal();
        refreshAllRows();
        applyFilters();
        if (typeFilter === 'tracker') renderTracker();
      }
      await pushPending();
    } catch {}
  }

  // ---- Tracker view ----
  const trackerView = document.getElementById('trackerView');
  const trackerBody = document.getElementById('trackerBody');
  const trackerEmpty = document.getElementById('trackerEmpty');

  function daysSince(iso) {
    const t = Date.parse(iso ? iso + (iso.length <= 19 ? 'Z' : '') : '');
    return t ? (Date.now() - t) / 86400000 : 0;
  }

  function fmtDate(iso) { return iso ? iso.slice(0, 10) : ''; }

  function showTrackerView(on) {
    ['.controls', '#jobTable', '#emptyState'].forEach(sel => {
      const el = document.querySelector(sel);
      if (el) el.hidden = on;
    });
    if (trackerView) trackerView.hidden = !on;
    if (on && trackerReady) renderTracker();
  }

  function renderTracker() {
    if (!trackerBody) return;
    const all = Object.values(apps).filter(a => TRACKED(a.status));
    const count = st => all.filter(a => a.status === st).length;
    const responded = count('interviewing') + count('offer') + count('rejected');
    const active = all.length - count('withdrawn');
    const rate = active ? Math.round(100 * responded / active) : 0;
    const followUps = all.filter(needsFollowUp).length;
    document.getElementById('trackerSummary').textContent = all.length
      ? `${all.length} application${all.length === 1 ? '' : 's'} · ${count('interviewing')} interviewing · ${count('offer')} offer${count('offer') === 1 ? '' : 's'} · ${rate}% heard back`
        + (followUps ? ` · ${followUps} need a follow-up` : '')
      : '';

    const chips = document.getElementById('trackerChips');
    chips.textContent = '';
    [['', 'All', all.length], ['applied', 'Applied', count('applied')], ['interviewing', 'Interviewing', count('interviewing')],
     ['offer', 'Offer', count('offer')], ['rejected', 'Rejected', count('rejected')], ['withdrawn', 'Withdrawn', count('withdrawn')]]
      .forEach(([val, label, n]) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'tracker-chip' + (trackerFilter === val ? ' active' : '');
        b.textContent = `${label} ${n}`;
        b.onclick = () => { trackerFilter = val; renderTracker(); };
        chips.appendChild(b);
      });

    const list = all.filter(a => !trackerFilter || a.status === trackerFilter)
      .sort((x, y) => (y.updated_at || '').localeCompare(x.updated_at || ''));
    trackerBody.textContent = '';
    list.forEach(a => trackerBody.appendChild(trackerRow(a)));
    if (trackerEmpty) trackerEmpty.classList.toggle('show', list.length === 0);
  }

  function needsFollowUp(a) {
    return a.status === 'applied' && daysSince(a.updated_at) >= FOLLOW_UP_DAYS;
  }

  function trackerRow(a) {
    const tr = document.createElement('tr');
    tr.className = 'tracker-row st-' + a.status;
    const cell = (child) => { const td = document.createElement('td'); if (child) td.append(child); tr.appendChild(td); return td; };

    cell(a.company || '');
    const roleTd = cell();
    if (/^https?:\/\//i.test(a.apply_url || '')) {
      const link = document.createElement('a');
      link.href = a.apply_url; link.target = '_blank'; link.rel = 'noopener';
      link.textContent = a.title || '(untitled)';
      roleTd.appendChild(link);
    } else {
      roleTd.textContent = a.title || '(untitled)';
    }
    if (a.sponsorship_flag && a.sponsorship_flag !== 'NA') {
      const tag = document.createElement('span');
      tag.className = 'sponsor-dot sponsor-' + a.sponsorship_flag.toLowerCase();
      tag.textContent = a.sponsorship_flag === 'RED' ? 'NO INFO' : a.sponsorship_flag;
      roleTd.append(' ', tag);
    }

    const stTd = cell();
    const sel = document.createElement('select');
    sel.className = 'status-select';
    ['applied', 'interviewing', 'offer', 'rejected', 'withdrawn', 'saved', 'none'].forEach(v => {
      const o = document.createElement('option');
      o.value = v; o.textContent = v === 'none' ? 'Remove from tracker' : STATUS_LABEL[v];
      sel.appendChild(o);
    });
    sel.value = a.status;
    sel.onchange = () => setStatus(a.job_key, sel.value);
    stTd.appendChild(sel);
    if (needsFollowUp(a)) {
      const f = document.createElement('span');
      f.className = 'badge badge-followup';
      f.textContent = 'Follow up?';
      f.title = `No update for ${FOLLOW_UP_DAYS}+ days`;
      stTd.append(' ', f);
    }

    cell(fmtDate(a.applied_at));
    cell(fmtDate(a.updated_at));

    const ta = document.createElement('textarea');
    ta.className = 'tracker-notes';
    ta.rows = 1;
    ta.placeholder = 'Add a note (recruiter, referral, next step…)';
    ta.value = a.notes || '';
    ta.onchange = () => setNotes(a.job_key, ta.value);
    cell(ta);
    cell('');
    return tr;
  }

  function updateTabCounts(rows) {
    let intern = 0;
    rows.forEach(r => { if (r.dataset.internship === '1') intern++; });
    const vals = Object.values(apps);
    const counts = { '': rows.length, fulltime: rows.length - intern, internship: intern,
      saved: vals.filter(a => a.status === 'saved').length, tracker: vals.filter(a => TRACKED(a.status)).length };
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

  document.addEventListener('change', e => {
    const sel = e.target.closest('#jobTable .status-select');
    if (sel) setStatus(rowKey(sel.closest('tr.job-row')), sel.value);
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
    const list = Object.values(apps).filter(a => a.status && a.status !== 'none')
      .sort((x, y) => (y.updated_at || '').localeCompare(x.updated_at || ''));
    if (!list.length) { showToast('Nothing in the tracker yet.', 3000); return; }
    const q = x => '"' + String(x == null ? '' : x).replace(/"/g, '""') + '"';
    const lines = ['company,title,location,h1b,status,applied_on,last_update,notes,url'];
    list.forEach(a => lines.push([a.company, a.title, a.location, a.sponsorship_flag, STATUS_LABEL[a.status],
      fmtDate(a.applied_at), fmtDate(a.updated_at), a.notes, a.apply_url].map(q).join(',')));
    const el = document.createElement('a');
    el.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    el.download = 'application_tracker.csv';
    el.click();
    URL.revokeObjectURL(el.href);
  });

  // Clicking Apply starts tracking the job as "Applied" (unless it is already further along)
  document.addEventListener('click', e => {
    const btn = e.target.closest('a.apply-btn');
    if (!btn) return;
    const row = btn.closest('tr.job-row');
    const st = statusOf(row);
    if (st === 'none' || st === 'saved') setStatus(rowKey(row), 'applied');
  });

  // Apply on page load
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

  // --- Run buttons: talk to scripts/control_server.py (works from file:// or from the server itself) ---
  const CONTROL_BASE = location.protocol.startsWith('http')
    ? ''
    : String(store.get('jobscraper_control_base', 'http://localhost:8765')).replace(/\/$/, '');
  const btns = {
    quick: document.getElementById('quickBtn'),
    full: document.getElementById('fullBtn'),
    recheck: document.getElementById('recheckBtn'),
  };
  const stopBtn = document.getElementById('stopBtn');
  const controlStatus = document.getElementById('controlStatus');
  const runPanel = document.getElementById('runPanel');
  const runBarFill = document.getElementById('runBarFill');
  const runDetail = document.getElementById('runDetail');
  const runBanner = document.getElementById('runBanner');
  const lastRunEl = document.getElementById('lastRun');
  let serverUp = false;
  let synced = false;
  let wasRunning = false;
  let startedHere = false;
  let pollTimer = null;
  const pageLoadedAt = Date.now() / 1000;

  const STAGE_LABEL = {
    starting: 'Starting…', scraping: 'Scraping job boards', filtering: 'Filtering jobs',
    saving: 'Saving to database', rendering: 'Building dashboard', done: 'Finished',
    error: 'Failed', stopped: 'Stopped',
  };
  const cap = w => w.charAt(0).toUpperCase() + w.slice(1);
  const fmtTime = secs => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;

  function setControlStatus(up) {
    serverUp = up;
    if (controlStatus) {
      controlStatus.innerHTML = up ? '● connected' : '● server offline';
      controlStatus.className = 'control-status ' + (up ? 'up' : 'down');
      controlStatus.title = up ? 'Control server connected' : 'Start it: python scripts/control_server.py';
    }
  }

  function renderStatus(s) {
    setControlStatus(true);
    Object.values(btns).forEach(b => { if (b) b.disabled = s.running; });
    if (stopBtn) stopBtn.hidden = !s.running;

    if (s.last_run && lastRunEl && !s.running) {
      const fin = (s.last_run.finished_at || '').replace('T', ' ').slice(0, 16);
      lastRunEl.textContent = `Last scan: ${fin} · ${s.last_run.total_passed} jobs matched · ${s.last_run.total_new} new`;
    }

    if (s.running) {
      runBanner.hidden = true;
      runPanel.hidden = false;
      runBarFill.style.width = (s.percent || 2) + '%';
      const plats = Object.entries(s.platforms || {})
        .filter(([, p]) => p.total > 0)
        .map(([n, p]) => `${cap(n)} ${p.done.toLocaleString()}/${p.total.toLocaleString()}`).join(' · ');
      let elapsed = '';
      if (s.started_at && s.server_time) {
        elapsed = ' · ' + fmtTime((Date.parse(s.server_time) - Date.parse(s.started_at)) / 1000) + ' elapsed';
      }
      runDetail.textContent = `${STAGE_LABEL[s.stage] || 'Working…'} — ${s.percent || 0}%${elapsed}` + (plats ? `\n${plats}` : '');
    } else {
      runPanel.hidden = true;
    }

    if (wasRunning && !s.running) {
      if (s.stage === 'done') {
        const r = s.result || {};
        showBanner('ok', `✓ Scan finished — ${r.passed ?? '?'} jobs matched, ${r.new ?? 0} new.`, true);
        if (startedHere) setTimeout(() => location.reload(), 2500);
      } else if (s.stage === 'error') {
        showBanner('err', `✗ Scan failed: ${s.message || 'see logs/run.log'}`, false);
      } else if (s.stage === 'stopped') {
        showBanner('warn', 'Scan stopped.', false);
      }
      startedHere = false;
    }
    wasRunning = s.running;

    // Someone else (another tab / terminal) refreshed the results while this page is open
    if (!s.running && s.dashboard_mtime > pageLoadedAt + 5 && !startedHere && runBanner.hidden) {
      showBanner('ok', 'Newer results are available.', true);
    }
  }

  function showBanner(kind, text, withReload) {
    runBanner.className = 'run-banner ' + kind;
    runBanner.textContent = text + ' ';
    if (withReload) {
      const b = document.createElement('button');
      b.className = 'toolbar-btn';
      b.textContent = 'Reload results';
      b.onclick = () => location.reload();
      runBanner.appendChild(b);
    }
    runBanner.hidden = false;
  }

  async function pingControl() {
    let delay = 10000;
    try {
      const r = await fetch(CONTROL_BASE + '/status', { cache: 'no-store' });
      if (!r.ok) throw new Error('bad status');
      const s = await r.json();
      renderStatus(s);
      if (!synced) { synced = true; syncFromServer(); } else { pushPending(); }
      if (s.running) delay = 2000;
    } catch {
      setControlStatus(false);
      synced = false;
      Object.values(btns).forEach(b => { if (b) b.disabled = true; });
      if (stopBtn) stopBtn.hidden = true;
      runPanel.hidden = true;
    }
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pingControl, delay);
  }

  async function post(path, body) {
    try {
      const r = await fetch(CONTROL_BASE + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      return await r.json();
    } catch {
      return { ok: false, message: 'Could not reach the server. Start it: python scripts/control_server.py' };
    }
  }

  Object.entries(btns).forEach(([mode, btn]) => {
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!serverUp) { showToast('Server offline. Run: python scripts/control_server.py', 4000); return; }
      const d = await post('/run', { mode });
      showToast(d.message || (d.ok ? 'Started' : 'Could not start'), 3500);
      if (d.ok) { startedHere = true; runBanner.hidden = true; }
      pingControl();
    });
  });

  if (stopBtn) {
    stopBtn.addEventListener('click', async () => {
      if (!confirm('Stop the running scan? Nothing is saved from a scan that has not finished.')) return;
      const d = await post('/stop');
      showToast(d.message || 'Stopped', 3000);
      pingControl();
    });
  }

  pingControl();

  initTracker();

  // Initial sort + filter (uses restored state or defaults)
  document.querySelectorAll('th.sortable').forEach(h => {
    h.classList.remove('sort-active', 'sort-desc');
    if (h.dataset.sort === sortCol) { h.classList.add('sort-active'); if (!sortAsc) h.classList.add('sort-desc'); }
  });
  applySort();
  applyFilters();
})();
