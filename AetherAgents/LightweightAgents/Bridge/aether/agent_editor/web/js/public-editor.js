/* Owner: Mission Control template creation/import and verified downloads.
 * Must never: implement installation, modify agent homes, call models, or grant consent for an agent.
 * Draft mechanics and validation belong to studio.js and the server authoring service.
 */
(() => {
  'use strict';
  function emptyDraft() {
    const now = new Date().toISOString();
    return {
      schema: 'agent-design-draft-v1',
      bundle_metadata: {draft_id: 'draft-' + crypto.randomUUID(), title: 'Untitled agent template',
        author: '', created_at: now, updated_at: now, revision: 1, lineage: [], notes: ''},
      identity: {tier: 'lightweight', name: '', role: '', agent_id_template: '', origin: '',
        intended_task_size: 'standard', capabilities: [], exclusions: [], values: [],
        handoff_expectations: '', disposition_and_working_style: []},
      deployment_preferences: {model_alias: '', backend_type: 'operator_local', context_limit: 8192,
        output_limit: 2048, request_timeout_sec: 60, run_timeout_sec: 300, tool_call_limit: 10,
        credential_reference: 'operator-managed:local-default', connection_status: 'not_connected'},
      strategies: [], mock_roster: []
    };
  }
  // Installed before DOMContentLoaded: the retained controller starts from this blank draft.
  window.aetherEditor = {emptyDraft};
  window.addEventListener('DOMContentLoaded', async () => {
    const app = window.__STUDIO_APP_INSTANCE__;
    const fields = document.getElementById('editor-fields');
    const status = document.getElementById('bridge-status');
    const access = document.getElementById('editor-access');
    let allowed = false, busy = false, custom;
    const say = message => { status.textContent = message; };
    const controls = () => { fields.disabled = !allowed || busy; };
    const discard = () => !app.isDirty || window.confirm('Replace this draft? Download it first if you want to keep your changes.');
    const identityTab = () => document.querySelector('[data-tab="identity"]').click();
    document.getElementById('editor-new').addEventListener('click', () => {
      if (!allowed || busy || !discard()) return;
      app.loadBundle(emptyDraft());
      custom?.reset();
      app.markDirty();
      document.getElementById('import-status-alert').style.display = 'none';
      identityTab();
      say('New blank template. Add its identity and author, then download it to keep your work.');
      document.getElementById('field-name').focus();
    });
    const input = document.getElementById('editor-import-file');
    document.getElementById('editor-import').addEventListener('click', () => {
      if (allowed && !busy) input.click();
    });
    input.addEventListener('change', async () => {
      const file = input.files[0]; input.value = '';
      if (!file || !allowed || busy || !discard()) return;
      busy = true; controls();
      try {
        // Retained importer validates before replacing the current draft and reports errors inline.
        const before = app.currentBundle;
        await app.processImportFile(file);
        if (before !== app.currentBundle) custom?.reset();
        identityTab();
      } catch (error) { say(error.message); }
      finally { busy = false; controls(); }
    });
    async function run(download) {
      if (!allowed || busy) return;
      busy = true; controls();
      try {
        const raw = JSON.stringify(app.currentBundle, null, 2) + '\n';
        const digest = await computeDigest(raw);
        say('Checking captured draft…');
        const response = await fetch('/api/agent-editor/' + (download ? 'export' : 'validate'), {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: raw
        });
        if (!response.ok) {
          const problem = await response.json();
          throw new Error((problem.errors || [problem.error || 'Validation failed.']).join('\n'));
        }
        if (download) {
          if (response.headers.get('X-Draft-SHA256') !== digest) throw new Error('Download does not match the captured draft.');
          const url = URL.createObjectURL(await response.blob());
          const link = document.createElement('a');
          link.href = url; link.download = 'agent-template-' + digest.slice(0, 12) + '.zip';
          document.body.append(link); link.click(); link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          if (JSON.stringify(app.currentBundle, null, 2) + '\n' === raw) app.markSaved(link.download, digest);
        } else {
          const report = await response.json();
          if (report.draft_sha256 !== digest || report.status !== 'TEMPLATE_VERIFIED') throw new Error('Validation did not match the captured draft.');
        }
        say((download ? 'Verified ZIP download prepared' : 'Template format verified') +
          '. No agent was installed or started. ' + (download ? 'Keep draft.json from the ZIP to edit later.' : 'Download the draft or ZIP to keep your work.'));
      } catch (error) { say(error.message); }
      finally { busy = false; controls(); }
    }
    document.getElementById('bridge-validate').addEventListener('click', () => run(false));
    document.getElementById('bridge-export').addEventListener('click', () => run(true));
    window.addEventListener('beforeunload', event => {
      if (app?.isDirty) { event.preventDefault(); event.returnValue = ''; }
    });
    try {
      const info = await (window.aetherAccess || fetch('/api/access/session', {cache: 'no-store'}).then(r => r.json()));
      allowed = !!app && !!info.user && (info.user.role === 'admin' || info.user.permissions.includes('manage_agents'));
      access.textContent = allowed ? 'Template authoring available · Manage Agents permission.' : 'View-only access. You can open and download saved custom revisions. Authoring and creation require Manage Agents permission.';
      const {attachCustomTemplates} = await import('./custom-templates.js');
      custom = await attachCustomTemplates(app, {allowed, user:info.user?.id, discard, setBusy:value=>{busy=value;controls();}});
    } catch (_) { access.textContent = 'Authoring access could not be checked. Refresh this page to retry.'; }
    controls();
  });
})();
