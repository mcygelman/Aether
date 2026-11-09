/* MOD-16 companion to the retained ADS controller. Project publication is separate from hiring. */
window.addEventListener('DOMContentLoaded', () => {
  const status = document.getElementById('bridge-status');
  const buttons = ['bridge-validate', 'bridge-export'].map(id => document.getElementById(id));
  async function run(download) {
    buttons.forEach(button => { button.disabled = true; });
    try {
      const app = window.__STUDIO_APP_INSTANCE__;
      if (!app) throw new Error('Editor has not finished loading.');
      // Capture once. Edits while the request runs cannot change the downloaded snapshot.
      const raw = JSON.stringify(app.currentBundle, null, 2) + '\n';
      const digest = await computeDigest(raw);
      status.textContent = 'Checking captured draft…';
      const response = await fetch('/api/agent-editor/' + (download ? 'export' : 'validate'), {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: raw
      });
      if (!response.ok) {
        const problem = await response.json();
        throw new Error((problem.errors || [problem.error || 'Validation failed.']).join('\n'));
      }
      if (download) {
        if (response.headers.get('X-Draft-SHA256') !== digest) throw new Error('Download does not match the captured draft.');
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url; link.download = 'agent-template-' + digest.slice(0, 12) + '.zip';
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        const report = await response.json();
        if (report.draft_sha256 !== digest || report.status !== 'TEMPLATE_VERIFIED') throw new Error('Validation did not match the captured draft.');
      }
      status.textContent = (download ? 'Download prepared' : 'Template verified') +
        ' for draft ' + digest.slice(0, 12) + '. ' +
        (JSON.stringify(app.currentBundle, null, 2) + '\n' === raw ? '' : 'The editor has changed since this snapshot. ') +
        'Validation covers format and integrity. Save the ZIP; it includes your complete draft and receipt. No agent has been hired.';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  }
  buttons[0].addEventListener('click', () => run(false));
  buttons[1].addEventListener('click', () => run(true));
});

/* Publication is explicit project input, never direct admission or dispatch. */
window.addEventListener('DOMContentLoaded', () => {
  const el = name => document.getElementById('publication-' + name);
  const key = 'aether-editor-publication-pending-v1';
  let pending = null, submitted = false, resolved = false, unrecorded = false, busy = false;
  const say = text => { el('status').textContent = text; };
  function controls() {
    el('prepare').disabled = busy || submitted;
    el('confirm').disabled = busy || !pending || resolved;
    el('inspect').disabled = busy || !pending;
    el('receipt').disabled = !pending;
    el('new').disabled = busy || !pending || (submitted && !resolved && !unrecorded);
    ['project', 'id', 'tools', 'actions'].forEach(id => { el(id).disabled = busy || submitted; });
  }
  function selection() {
    const app = window.__STUDIO_APP_INSTANCE__;
    if (!app) throw new Error('Editor has not finished loading.');
    const names = id => el(id).value.split(',').map(x => x.trim()).filter(Boolean);
    return {project_id: el('project').value, template_id: el('id').value.trim(),
      draft_text: JSON.stringify(app.currentBundle, null, 2) + '\n', tools: names('tools'), actions: names('actions')};
  }
  function show() {
    const p = pending.proposal, s = p.selection;
    el('review').textContent = 'Project: ' + s.project_id + '\nTemplate: ' + s.template_id +
      '\nTier: ' + p.validation.tier + '\nOperator: ' + p.operator.display_name +
      '\nTools: ' + (s.tools.join(', ') || 'none') + '\nActions: ' + (s.actions.join(', ') || 'none') +
      '\nDraft SHA-256: ' + p.validation.draft_sha256 + '\nTemplate SHA-256: ' + p.validation.artifact_sha256 +
      '\nRequest: ' + pending.request_id + '\n\n' + p.authority + '\n\nExact template:\n' + p.template_text;
  }
  async function post(operation, body) {
    const response = await fetch('/api/agent-editor/publication-' + operation,
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Publication request failed.');
    return result;
  }
  function received(result) {
    if (result.status === 'PUBLISHED') {
      // Inspect may return a different record for a reused ID: never acknowledge it as ours.
      if (JSON.stringify(result.record) !== JSON.stringify(pending)) {
        const canonical = value => JSON.stringify(value, function(k, v) {
          return v && typeof v === 'object' && !Array.isArray(v) ? Object.keys(v).sort().reduce((o, k) => {o[k] = v[k]; return o;}, {}) : v;
        });
        if (canonical(result.record) !== canonical(pending)) throw new Error('Saved record differs from this request; retain it for inspection.');
      }
      resolved = true;
      const pid = encodeURIComponent(pending.proposal.selection.project_id);
      el('launch').href = '/projects/staffing-launch?project_id=' + pid;
      el('decisions').href = '/projects/staff-decisions?project_id=' + pid;
      el('next').hidden = false;
      say('Publication verified on disk. Continue through project staffing review and member admission. No member was created by publication.');
    } else {
      unrecorded = true;
      say('No complete publication is recorded. Retry this exact confirmation, or save its request and start another review if the context changed. This observation does not cancel an in-flight request.');
    }
  }
  async function run(work) {
    if (busy) return;
    busy = true; controls();
    try { await work(); }
    catch (error) { say(error.message + (submitted ? ' Keep the retained request; check or retry this same confirmation.' : '')); }
    finally { busy = false; controls(); }
  }
  el('prepare').addEventListener('click', () => run(async () => {
    const preview = await post('preview', selection());
    pending = {request_id: crypto.randomUUID(), ...preview, confirmed: true};
    submitted = false; resolved = false; unrecorded = false; show();
    say('Review the captured template and project, then confirm. Editing the draft requires a fresh review.');
  }));
  el('confirm').addEventListener('click', () => run(async () => {
    if (!submitted && JSON.stringify(selection()) !== JSON.stringify(pending.proposal.selection)) {
      // Property order is not significant in server JSON.
      const s = selection(), p = pending.proposal.selection;
      if (Object.keys(s).some(k => JSON.stringify(s[k]) !== JSON.stringify(p[k]))) throw new Error('The draft or project selection changed. Review publication again.');
    }
    // Save BEFORE the first write. Restoring a tab never automatically confirms.
    sessionStorage.setItem(key, JSON.stringify(pending));
    submitted = true; unrecorded = false;
    received(await post('confirm', pending));
  }));
  el('inspect').addEventListener('click', () => run(async () => {
    received(await post('inspect', {project_id: pending.proposal.selection.project_id, request_id: pending.request_id}));
  }));
  function downloadRequest() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(pending, null, 2) + '\n'], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'publication-' + pending.request_id + '.json';
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  el('receipt').addEventListener('click', downloadRequest);
  el('new').addEventListener('click', () => {
    downloadRequest();
    sessionStorage.removeItem(key); pending = null; submitted = false; resolved = false; unrecorded = false;
    el('review').textContent = ''; el('next').hidden = true; controls(); say('Choose the next template and project.');
  });
  try {
    const saved = sessionStorage.getItem(key);
    if (saved) { pending = JSON.parse(saved); show(); submitted = true; say('Restored the exact pending request. Check its saved publication before retrying.'); }
  } catch (error) { pending = null; say('Could not restore a pending request: ' + error.message); }
  controls();
  fetch('/api/agent-editor/projects').then(async response => {
    const data = await response.json();
    if (!response.ok || !data.available) throw new Error(data.error || 'Project publication is unavailable.');
    for (const p of data.projects) {
      const option = document.createElement('option'); option.value = p.id;
      option.textContent = p.name + ' (' + p.id + ')' + (p.publication_available ? '' : ' — ' + p.reason);
      option.disabled = !p.publication_available; el('project').appendChild(option);
    }
    if (!pending) say(data.projects.length ? 'Choose a project before staffing begins.' : 'Create a governed project first.');
  }).catch(error => { if (!pending) say(error.message); });
});

window.addEventListener('DOMContentLoaded', () => {
  const el = key => document.getElementById('consent-' + key);
  const fields = ['name', 'purpose', 'charter', 'starting_memory', 'memory_readers', 'retirement_memory_policy'];
  let reviewed = null, captured = null;
  const current = () => Object.fromEntries(fields.map(key => [key, el(key).value]));
  const say = text => { el('status').textContent = text; };
  fields.forEach(key => el(key).addEventListener('input', () => {
    el('download').disabled = true; el('prepare').disabled = true; say('The offer changed. Review its exact contents again.');
  }));
  fetch('/api/agent-editor/consent-policy').then(async response => {
    if (!response.ok) throw new Error('Disclosure is unavailable.');
    const policy = await response.json();
    el('core').textContent = policy.version + '\n\n' + policy.disclosure.join('\n\n');
  }).catch(error => { el('core').textContent = error.message; });
  el('review').addEventListener('click', async () => {
    el('review').disabled = true; el('download').disabled = true; el('prepare').disabled = true;
    try {
      const snapshot = current();
      const response = await fetch('/api/agent-editor/consent-offer', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(snapshot)});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || 'Offer review failed.');
      if (fields.some(key => value.proposal.addendum[key] !== snapshot[key])) throw new Error('Offer differs from the captured draft.');
      reviewed = value; captured = snapshot;
      el('preview').textContent = JSON.stringify(value, null, 2);
      el('download').disabled = JSON.stringify(current()) !== JSON.stringify(captured);
      el('prepare').disabled = el('download').disabled;
      say(el('download').disabled ? 'The editor changed during review. Review again before downloading.' :
        'Exact offer prepared. Its full disclosure travels with it. No model was called and no identity was created.');
    } catch(error) { say(error.message); }
    finally { el('review').disabled = false; }
  });
  el('download').addEventListener('click', () => {
    if (!reviewed || JSON.stringify(current()) !== JSON.stringify(captured)) {
      el('download').disabled = true; say('Review the changed offer first.'); return;
    }
    const url = URL.createObjectURL(new Blob([JSON.stringify(reviewed, null, 2) + '\n'], {type:'application/json'}));
    const link = document.createElement('a');link.href = url;link.download = 'heavyweight-offer-' + reviewed.sha256.slice(0,12) + '.json';
    document.body.appendChild(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url),1000);
    say('Offer download prepared. This preserves a proposal, not a consent record.');
  });
  el('prepare').addEventListener('click', async () => {
    if (!reviewed || JSON.stringify(current()) !== JSON.stringify(captured)) {
      el('prepare').disabled = true; say('Review the changed offer first.'); return;
    }
    el('prepare').disabled = true; el('review').disabled = true;
    try {
      const response = await fetch('/api/heavyweight-editor/prepare', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({kind:'founding',
          selection:{offer:reviewed,operator_answers:null}})});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || 'Configured runtime preparation is unavailable.');
      try { sessionStorage.setItem('aether-heavyweight-desk-request-v1',value.request_id); }
      catch (_) { say('Prepared request ' + value.request_id + '. Open the lifecycle desk and select this ID. No runtime was reserved.'); return; }
      window.location.assign('/agent-editor/lifecycle.html');
    } catch (error) { say(error.message + ' Inspect the lifecycle desk before preparing another request.'); }
    finally { el('review').disabled = false; }
  });
});
