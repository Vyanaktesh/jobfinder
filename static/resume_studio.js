// Resume Studio: paste a tailoring patch, validate it, build the PDF, keep a history.
// Talks to the local dashboard server (scripts/control_server.py). No LLM calls happen here.
(() => {
  const $ = id => document.getElementById(id);
  const view = $('studioView');
  if (!view) return;

  let savedBase = 'http://localhost:8765';
  try { savedBase = JSON.parse(localStorage.getItem('jobscraper_control_base')) || savedBase; } catch {}
  const BASE = location.protocol.startsWith('http') ? '' : String(savedBase).replace(/\/$/, '');

  const el = {
    note: $('studioNote'), base: $('studioBase'), company: $('studioCompany'), role: $('studioRole'),
    jd: $('studioJd'), patch: $('studioPatch'), paste: $('studioPaste'), validate: $('studioValidate'),
    build: $('studioBuild'), dlPdf: $('studioDlPdf'), dlTex: $('studioDlTex'), copyTex: $('studioCopyTex'),
    status: $('studioStatus'), messages: $('studioMessages'), preview: $('studioPreview'),
    histBody: $('studioHistoryBody'), histEmpty: $('studioHistoryEmpty'),
  };
  const state = { loaded: false, online: false, baseCache: {}, last: null, jobId: null, timer: null };

  async function api(path, body) {
    try {
      const r = await fetch(BASE + path, body === undefined ? { cache: 'no-store' } : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await r.json();
      return Object.assign({ _status: r.status }, data);
    } catch {
      return { _offline: true };
    }
  }

  const toast = (msg, ms) => {
    const t = $('toast');
    if (!t) return;
    t.textContent = msg; t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), ms || 3000);
  };

  function setOnline(on) {
    state.online = on;
    [el.validate, el.build].forEach(b => { b.disabled = !on; });
    if (!on) {
      el.note.className = 'studio-note warn';
      el.note.textContent = 'The dashboard server is not running, so Resume Studio cannot validate or build. Start it with .\\start_dashboard.bat.';
    }
  }

  // ---- loading ----
  function setNote(text, warn) {
    el.note.className = 'studio-note' + (warn ? ' warn' : '');
    el.note.textContent = text;
  }

  async function loadBases() {
    const d = await api('/api/resumes');
    if (d._offline) { setOnline(false); return; }
    if (!Array.isArray(d.bases)) {
      // The server answered but does not know Resume Studio: it is still the old process.
      setOnline(false);
      setNote('The dashboard server is running older code and does not have Resume Studio yet. Close its window '
            + '(or press Ctrl+C in it) and start it again with .\\start_dashboard.bat, then reload this page.', true);
      return;
    }
    setOnline(true);
    const keep = el.base.value;
    el.base.textContent = '';
    d.bases.forEach(b => {
      const o = document.createElement('option');
      o.value = b.id; o.textContent = b.id.replace(/_/g, ' ');
      el.base.appendChild(o);
    });
    if (keep && d.bases.some(b => b.id === keep)) el.base.value = keep;

    const msgs = [];
    if (d.bases.length === 0) {
      msgs.push(`No base resumes found in ${d.data_dir}.`);
      (d.problems || []).forEach(p => msgs.push(p));
      msgs.push('Load yours with: python scripts/import_resume_kit.py <resume_kit folder or .zip>');
      [el.validate, el.build].forEach(b => { b.disabled = true; });
    }
    if (d.using_sample) msgs.push('Showing the SAMPLE resume, not yours. Load yours with: python scripts/import_resume_kit.py <resume_kit folder or .zip> '
                                + '(or drop the resume_kit folder into the repo folder and restart the server).');
    else if (d.bases.length && (d.problems || []).length) (d.problems || []).forEach(p => msgs.push('Skipped: ' + p));
    if (d.latex === null) msgs.push('No LaTeX compiler found: builds will save the .tex only (open it in Overleaf, or install MiKTeX / TeX Live).');
    setNote(msgs.join(' '), msgs.length > 0);
    state.loaded = true;
    await ensureBase(el.base.value);
  }

  async function ensureBase(id) {
    if (!id) return null;
    if (!state.baseCache[id]) {
      const d = await api('/api/resumes/' + encodeURIComponent(id));
      if (d.base) state.baseCache[id] = d.base;
    }
    return state.baseCache[id] || null;
  }

  async function loadHistory() {
    const d = await api('/api/resumes/history');
    if (d._offline) return;
    el.histBody.textContent = '';
    const rows = d.history || [];
    el.histEmpty.classList.toggle('show', rows.length === 0);
    rows.forEach(h => {
      const tr = document.createElement('tr');
      const cell = (v) => { const td = document.createElement('td'); if (v instanceof Node) td.appendChild(v); else td.textContent = v == null ? '' : v; tr.appendChild(td); return td; };
      cell((h.created_at || '').slice(0, 16));
      cell(h.company); cell(h.role); cell((h.base_id || '').replace(/_/g, ' '));
      const pg = cell(h.pages == null ? '?' : h.pages);
      if (h.pages > 1) pg.className = 'studio-bad';
      const files = document.createElement('span');
      const link = (ext, label) => {
        const a = document.createElement('a');
        a.href = `${BASE}/api/resumes/file/${h.id}.${ext}`; a.target = '_blank'; a.rel = 'noopener'; a.textContent = label;
        return a;
      };
      if (h.pdf_path) files.append(link('pdf', 'PDF'), ' · ');
      files.appendChild(link('tex', '.tex'));
      cell(files);
      el.histBody.appendChild(tr);
    });
  }

  // ---- patch parsing ----
  function parsePatch() {
    let text = el.patch.value.trim();
    if (!text) return { empty: true };
    text = text.replace(/^```[a-zA-Z]*\s*/, '').replace(/\s*```$/, '').trim();   // tolerate a pasted code fence
    try {
      const p = JSON.parse(text);
      if (p === null || typeof p !== 'object' || Array.isArray(p)) return { error: 'The patch must be a JSON object ({ ... }).' };
      return { patch: p };
    } catch (e) {
      return { error: 'That is not valid JSON: ' + e.message };
    }
  }

  // ---- preview ----
  function applyPatch(base, patch) {
    const r = JSON.parse(JSON.stringify(base));
    ['tagline', 'skills', 'projects'].forEach(k => { if (patch[k] !== undefined) r[k] = patch[k]; });
    Object.entries(patch.experience_bullets || {}).forEach(([i, b]) => { if (r.experience[+i]) r.experience[+i].bullets = b; });
    return r;
  }

  function richText(parent, text) {                       // **bold** without innerHTML
    String(text).split('**').forEach((part, i) => {
      if (!part) return;
      if (i % 2) { const b = document.createElement('b'); b.textContent = part; parent.appendChild(b); }
      else parent.appendChild(document.createTextNode(part));
    });
  }

  function line(parent, text, changed, cls) {
    const d = document.createElement('div');
    d.className = 'pv-line' + (cls ? ' ' + cls : '') + (changed ? ' changed' : '');
    richText(d, text);
    parent.appendChild(d);
  }

  function renderPreview(base, patch) {
    el.preview.textContent = '';
    if (!base || !patch) {
      const p = document.createElement('p'); p.className = 'studio-hint'; p.textContent = 'Paste a patch to see the result.';
      el.preview.appendChild(p); return;
    }
    let res;
    try { res = applyPatch(base, patch); } catch { return; }
    const baseBullets = new Set([...base.experience.flatMap(e => e.bullets), ...base.projects.flatMap(p => p.bullets)]);
    const baseSkills = new Set(base.skills.map(s => `${s.label}: ${s.items}`));
    const baseProjHeads = new Set(base.projects.map(p => `${p.name} | ${p.stack}${p.tag ? ' | ' + p.tag : ''}`));

    const sec = t => { const h = document.createElement('div'); h.className = 'pv-head'; h.textContent = t; el.preview.appendChild(h); };
    sec('Tagline');
    line(el.preview, res.tagline || '', res.tagline !== base.tagline);
    sec('Technical skills');
    (res.skills || []).forEach(s => line(el.preview, `**${s.label}:** ${s.items}`, !baseSkills.has(`${s.label}: ${s.items}`)));
    sec('Experience');
    (res.experience || []).forEach(e => {
      line(el.preview, `**${e.title}**, ${e.org}`, false, 'pv-sub');
      (e.bullets || []).forEach(b => line(el.preview, b, !baseBullets.has(b), 'pv-bullet'));
    });
    sec('Projects');
    (res.projects || []).forEach(p => {
      const head = `${p.name} | ${p.stack}${p.tag ? ' | ' + p.tag : ''}`;
      line(el.preview, `**${p.name}** | ${p.stack}${p.tag ? ' | ' + p.tag : ''}`, !baseProjHeads.has(head), 'pv-sub');
      (p.bullets || []).forEach(b => line(el.preview, b, !baseBullets.has(b), 'pv-bullet'));
    });
  }

  // ---- messages ----
  function showMessages({ errors = [], warnings = [], missing = [], notes = '' }) {
    el.messages.textContent = '';
    const list = (items, cls, title) => {
      if (!items.length) return;
      const box = document.createElement('div'); box.className = 'msg-box ' + cls;
      const h = document.createElement('b'); h.textContent = title; box.appendChild(h);
      const ul = document.createElement('ul');
      items.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
      box.appendChild(ul); el.messages.appendChild(box);
    };
    list(errors, 'err', `${errors.length} problem${errors.length === 1 ? '' : 's'} to fix`);
    list(warnings, 'warn', 'Warnings');
    if (missing.length) {
      const box = document.createElement('div'); box.className = 'msg-box info';
      const h = document.createElement('b'); h.textContent = 'Missing keywords (not on the resume)'; box.appendChild(h);
      const chips = document.createElement('div'); chips.className = 'kw-chips';
      missing.forEach(k => { const c = document.createElement('span'); c.className = 'kw-chip'; c.textContent = k; chips.appendChild(c); });
      box.appendChild(chips); el.messages.appendChild(box);
    }
    if (notes) {
      const box = document.createElement('div'); box.className = 'msg-box info';
      const h = document.createElement('b'); h.textContent = 'Match notes'; box.appendChild(h);
      const p = document.createElement('p'); p.textContent = notes; box.appendChild(p);
      el.messages.appendChild(box);
    }
  }

  // ---- actions ----
  async function onPatchChanged() {
    const parsed = parsePatch();
    state.last = null; setDownloads(null);
    if (parsed.empty) { showMessages({}); renderPreview(null, null); return; }
    if (parsed.error) { showMessages({ errors: [parsed.error] }); renderPreview(null, null); return; }
    const patch = parsed.patch;
    if (typeof patch.base === 'string' && [...el.base.options].some(o => o.value === patch.base)) el.base.value = patch.base;
    renderPreview(await ensureBase(el.base.value), patch);
    clearTimeout(state.timer);
    state.timer = setTimeout(runValidate, 500);
  }

  async function runValidate() {
    const parsed = parsePatch();
    if (parsed.empty || parsed.error || !state.online) return null;
    const d = await api('/api/resumes/validate', { base: el.base.value, patch: parsed.patch });
    if (d._offline) { setOnline(false); return null; }
    showMessages({ errors: d.errors || [], warnings: d.warnings || [],
                   missing: parsed.patch.missing_keywords || [], notes: parsed.patch.match_notes || '' });
    el.status.textContent = d.ok ? 'Patch looks good.' : '';
    el.status.className = 'studio-status' + (d.ok ? ' ok' : '');
    return d;
  }

  function setDownloads(build) {
    el.dlPdf.disabled = !(build && build.pdf_built);
    el.dlTex.disabled = !build;
    el.copyTex.disabled = !build;
  }

  async function runBuild() {
    const parsed = parsePatch();
    if (parsed.empty) { showMessages({ errors: ['Paste a tailoring patch first.'] }); return; }
    if (parsed.error) { showMessages({ errors: [parsed.error] }); return; }
    el.build.disabled = true; el.build.textContent = 'Building…';
    el.status.textContent = ''; el.status.className = 'studio-status';
    const d = await api('/api/resumes/build', {
      base: el.base.value, patch: parsed.patch, company: el.company.value, role: el.role.value,
      job_id: state.jobId, jd_text: el.jd.value });
    el.build.disabled = !state.online; el.build.textContent = 'Build PDF';
    if (d._offline) { setOnline(false); return; }
    showMessages({ errors: d.errors || [], warnings: d.warnings || [],
                   missing: parsed.patch.missing_keywords || [], notes: parsed.patch.match_notes || '' });
    if (!d.ok) { el.status.textContent = 'Not built. Fix the problems on the right.'; el.status.className = 'studio-status bad'; return; }
    state.last = d; setDownloads(d);
    if (d.pdf_built) {
      const one = d.pages === 1;
      el.status.textContent = d.pages == null ? 'PDF built.' : `PDF built: ${d.pages} page${d.pages === 1 ? '' : 's'}${one ? '' : ' (over one page!)'}.`;
      el.status.className = 'studio-status ' + (d.pages > 1 ? 'bad' : 'ok');
    } else {
      el.status.textContent = '.tex saved (no PDF: see the warning).'; el.status.className = 'studio-status warn';
    }
    loadHistory();
  }

  el.patch.addEventListener('input', onPatchChanged);
  el.base.addEventListener('change', onPatchChanged);
  el.validate.addEventListener('click', async () => { const d = await runValidate(); if (!d) toast('Paste a valid JSON patch first.'); });
  el.build.addEventListener('click', runBuild);
  el.paste.addEventListener('click', async () => {
    try { el.patch.value = await navigator.clipboard.readText(); onPatchChanged(); }
    catch { toast('Could not read the clipboard. Paste into the box with Ctrl+V.', 3500); }
  });
  el.dlPdf.addEventListener('click', () => state.last && window.open(`${BASE}/api/resumes/file/${state.last.id}.pdf`, '_blank'));
  el.dlTex.addEventListener('click', () => state.last && window.open(`${BASE}/api/resumes/file/${state.last.id}.tex`, '_blank'));
  el.copyTex.addEventListener('click', async () => {
    if (!state.last) return;
    try {
      const r = await fetch(`${BASE}/api/resumes/file/${state.last.id}.tex`);
      await navigator.clipboard.writeText(await r.text());
      toast('LaTeX copied.');
    } catch { toast('Copy failed. Use Download .tex instead.', 3500); }
  });

  window.ResumeStudio = {
    async onShow() { await loadBases(); loadHistory(); },
    // "Tailor resume" button on a tracker row
    async openFor({ company, role, key }) {
      await loadBases();
      el.company.value = company || '';
      el.role.value = role || '';
      state.jobId = null;
      if (key) {
        const d = await api('/api/job?key=' + encodeURIComponent(key));
        if (d.job) {
          if (!el.jd.value.trim()) el.jd.value = d.job.description || '';
          if (!el.company.value) el.company.value = d.job.company || '';
        }
      }
      loadHistory();
      el.patch.focus();
    },
  };

  // The dashboard remembers the last tab, so a reload can open directly on this panel before this
  // script has loaded (and so before the tab click that normally starts it). Start it now.
  if (!view.hidden) window.ResumeStudio.onShow();
})();
