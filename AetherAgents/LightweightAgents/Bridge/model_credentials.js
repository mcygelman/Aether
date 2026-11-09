/* Secrets are requested only for explicit reveal/copy, never kept in browser storage. */
(() => {
  async function api(path, payload) {
    const response = await fetch(new URL(path.slice(1), location.href), payload === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'API key request failed.');
    return value;
  }
  let state = null, pending = false, editor = null, epoch = 0, setup = false, resetReview = null, addAfterUnlock = false;
  const byId = id => document.getElementById(id);
  const fields = ['vaultPassword', 'vaultConfirm', 'vaultKey', 'vaultResetPassword', 'vaultResetConfirm'];
  const clear = () => fields.forEach(id => { byId(id).value = ''; });
  function feedback(message, error = false) {
    const node = byId('vaultStatus');
    node.textContent = message;
    node.classList.toggle('error', error);
    node.classList.toggle('notice', error);
    node.setAttribute('role', error ? 'alert' : 'status');
  }
  function validate(action, payload) {
    for (const id of [...fields, 'vaultName']) byId(id).removeAttribute('aria-invalid');
    const fail = (id, message) => {
      feedback(message, true);
      byId(id).setAttribute('aria-invalid', 'true');
      byId(id).focus();
      return false;
    };
    if (['create', 'unlock', 'reset'].includes(action)) {
      const passwordField = action === 'reset' ? 'vaultResetPassword' : 'vaultPassword';
      const confirmField = action === 'reset' ? 'vaultResetConfirm' : 'vaultConfirm';
      if (!payload.password) return fail(passwordField, 'Enter your API management password.');
      if (action !== 'unlock' && Array.from(payload.password).length < 6)
        return fail(passwordField, 'Choose an API management password with at least 6 characters.');
      if (action !== 'unlock' && payload.password !== payload.confirmation)
        return fail(confirmField, 'The passwords do not match. Enter the same password in both fields.');
      if (action === 'reset' && !byId('vaultResetConfirmDelete').checked)
        return fail('vaultResetConfirmDelete', 'Confirm that all saved API keys will be deleted.');
    }
    if (['add', 'rename'].includes(action) && !payload.label.trim())
      return fail('vaultName', 'Enter a label for this key, such as Devkey.');
    if (['add', 'replace'].includes(action)) {
      payload.api_key = payload.api_key.trim();
      if (!payload.api_key) return fail('vaultKey', 'Paste your API key.');
      if (!/^[\x21-\x7e]{1,4096}$/.test(payload.api_key))
        return fail('vaultKey', 'The API key must not contain spaces or line breaks.');
    }
    return true;
  }
  const timers = new Set();
  const providerName = value => ({deepseek: 'DeepSeek', anthropic: 'Claude (Anthropic)', openai: 'OpenAI'})[value] || 'OpenAI-compatible';
  function hideSecrets() {
    for (const timer of timers) clearTimeout(timer);
    timers.clear();
    for (const input of byId('vaultList').querySelectorAll('input')) {
      input.value = ''; input.hidden = true;
    }
    for (const button of byId('vaultList').querySelectorAll('[data-reveal]')) button.textContent = 'Reveal';
  }
  const adminOnly = 'Only an Admin can set up API keys here. Ask an Admin to add a key and assign it to you.';
  const describe = () => !state?.supported
    ? 'Password-protected key storage is available in the Windows app.'
    : state.can_manage === false && (!state.configured || !state.can_assign) ? 'Keys assigned to you are listed under Assigned API keys.'
    : !state.configured ? 'Create your API management password first. It protects all the keys you add afterward.'
    : state.unlocked ? `${state.keys.length} saved API ${state.keys.length === 1 ? 'key' : 'keys'}. Management unlocked for this signed-in session.`
    : 'The key store is locked. Enter your API management password to view saved keys or add a new key.';
  function render() {
    const usable = !!state?.supported, unlocked = !!state?.unlocked;
    const manage = state?.can_manage !== false, assign = !!state?.can_assign;
    const creating = usable && !state.configured;
    const resetting = resetReview !== null;
    byId('vaultPasswordState').textContent = !usable ? 'Password setup is unavailable.'
      : creating && !manage ? 'API keys are not set up yet.'
      : creating ? 'API management password not set.'
      : unlocked ? 'Password set · Management unlocked for this signed-in session.'
      : 'Password set · Management is locked; assigned usage continues.';
    byId('vaultSetup').hidden = !creating || setup;
    byId('vaultPasswordForm').hidden = !usable || resetting || (creating ? !setup : unlocked);
    byId('vaultCreate').hidden = !creating || !setup;
    byId('vaultSetupCancel').hidden = !creating || !setup;
    byId('vaultUnlock').hidden = !usable || !state.configured || unlocked || resetting;
    byId('vaultLock').hidden = !unlocked || resetting;
    byId('vaultReset').hidden = !usable || !state.configured || resetting;
    byId('vaultResetPanel').hidden = !resetting;
    byId('vaultConfirmLabel').hidden = !creating;
    byId('vaultPasswordHelp').hidden = !creating;
    byId('vaultKeys').hidden = !unlocked || resetting;
    byId('vaultKeysUnavailable').hidden = unlocked && !resetting;
    byId('vaultKeysUnavailable').textContent = resetting ? 'Finish or cancel the password reset to return to key management.'
      : !manage && (!assign || !state?.configured) ? adminOnly
      : state?.configured ? 'Unlock with your API management password to view, add or manage keys.'
      : 'Create your API management password first to enable key management.';
    byId('vaultAdd').hidden = !usable || !state.configured || !!editor || resetting;
    byId('vaultSave').hidden = !unlocked || !editor;
    byId('vaultCancel').hidden = !unlocked || !editor;
    byId('vaultEditor').hidden = !unlocked || !editor;
    byId('vaultNameLabel').hidden = editor?.action === 'replace';
    byId('vaultProviderLabel').hidden = !!editor && editor.action !== 'add';
    byId('vaultKeyLabel').hidden = editor?.action === 'rename';
    byId('vaultEditorTitle').textContent = !editor || editor.action === 'add' ? 'Add API key'
      : `${editor.action === 'rename' ? 'Rename' : 'Replace key for'} “${editor.label}”`;
    byId('vaultSave').textContent = editor?.action === 'rename' ? 'Save label' : editor?.action === 'replace' ? 'Save replacement key' : 'Save key';
    byId('vaultPassword').autocomplete = state?.configured ? 'current-password' : 'new-password';
    for (const node of byId('apiVault').querySelectorAll('input,select,button')) node.disabled = pending || !usable;
    byId('vaultReset').disabled = pending || !usable || !state?.revision;
    byId('vaultResetApply').disabled = pending || !usable || !resetting || !byId('vaultResetConfirmDelete').checked;
    for(const id of ['vaultSetup','vaultCreate','vaultReset','vaultAdd','vaultSave']) if(!manage) byId(id).hidden=true;
    if(!manage&&!assign){byId('vaultPasswordForm').hidden=true;byId('vaultUnlock').hidden=true;}
    for (const node of byId('vaultKeys').querySelectorAll('input,select,button'))
      node.disabled = pending || !usable || !unlocked || resetting;
  }
  window.vaultControls = render;
  function begin(action, key) {
    hideSecrets(); clear();
    editor = {action, id:key?.id, label:key?.label};
    byId('vaultName').value = key?.label || '';
    byId('vaultProvider').value = key?.provider || 'deepseek';
    render();
    byId(action === 'replace' ? 'vaultKey' : 'vaultName').focus();
  }
  function list() {
    hideSecrets();
    const box = byId('vaultList'); box.replaceChildren();
    if (!state?.unlocked) return;
    if (!state.keys.length) {
      const empty = document.createElement('p'); empty.textContent = 'No saved keys yet. Add an API key below.'; box.append(empty);
    }
    for (const key of state.keys) {
      const row = document.createElement('article'); row.className = 'vault-row'; row.dataset.keyId = key.id;
      const title = document.createElement('h3'); title.textContent = key.label;
      const meta = document.createElement('p'); meta.className = 'muted'; meta.textContent = `${providerName(key.provider)} · ${key.masked}`;
      const usage = document.createElement('small');
      usage.textContent = state.usage_known ? (key.deployments.length ? `Used by: ${key.deployments.join(', ')}` : 'Not used by a saved model connection.') : 'Which connections use it is currently unavailable.';
      const secret = document.createElement('input'); secret.readOnly = true; secret.hidden = true; secret.autocomplete = 'off'; secret.spellcheck = false; secret.setAttribute('aria-label', `Revealed key for ${key.label}`);
      const actions = document.createElement('div'); actions.className = 'actions';
      const button = (text, fn) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = text; b.onclick = fn; actions.append(b); return b; };
      const reveal = button('Reveal', async () => {
        if (!secret.hidden) { hideSecrets(); return; }
        await expose(key, value => {
          secret.value = value; secret.hidden = false; reveal.textContent = 'Hide';
          const timer = setTimeout(hideSecrets, 30000); timers.add(timer);
        });
      });
      reveal.dataset.reveal = '';
      button('Copy', () => expose(key, async value => {
        try { await navigator.clipboard.writeText(value); byId('vaultStatus').textContent = 'Key copied. Your system clipboard now contains the key.'; }
        catch (_) { byId('vaultStatus').textContent = 'Clipboard access was unavailable. Use Reveal to select and copy the key.'; }
      }));
      button('Rename', () => begin('rename', key));
      button('Replace', () => begin('replace', key));
      const remove = button('Delete', async () => {
        if (pending) return;
        // Refresh usage before asking; never change deployments as a side effect.
        await refresh();
        const current = state?.keys?.find(k => k.id === key.id);
        if (!current) return;
        const use = state.usage_known ? (current.deployments.length ? `Connections using it: ${current.deployments.join(', ')}. They will need another key.` : 'No saved connection uses it.') : 'Connection usage could not be checked. Any connection using this key will need another key.';
        // Confirmed inline in the key's own card (GUI review S9), never by the browser's confirm dialog.
        const live = byId('vaultList').querySelector(`[data-key-id="${CSS.escape(key.id)}"]`);
        if (!live) return;
        live.querySelector('.vault-confirm')?.remove();
        const box = document.createElement('div'); box.className = 'vault-confirm'; box.setAttribute('role', 'alert');
        const text = document.createElement('p');
        text.textContent = `Delete “${current.label}” from this computer? ${use} This removes the saved key, not your provider account or connection settings.`;
        const yes = document.createElement('button'); yes.type = 'button'; yes.className = 'vault-delete'; yes.textContent = 'Delete key';
        const no = document.createElement('button'); no.type = 'button'; no.textContent = 'Keep it';
        yes.onclick = () => act('delete', {id:key.id});
        no.onclick = () => { box.remove(); remove.focus(); };
        const buttons = document.createElement('div'); buttons.className = 'actions'; buttons.append(yes, no);
        box.append(text, buttons); live.append(box); no.focus();
      }); remove.className = 'vault-delete';
      if(state.can_manage===false)actions.replaceChildren();
      row.append(title, meta, usage, secret, actions); box.append(row);
    }
  }
  function accept(value) { state = value; list(); window.dispatchEvent(new Event('aether-key-management')); }
  async function refresh() {
    const requestEpoch = ++epoch;
    try {
      const value = await api('/api/credentials');
      if (requestEpoch !== epoch) return;
      accept(value); feedback(describe());
    } catch (_) {
      if (requestEpoch !== epoch) return;
      state = null; list(); byId('vaultStatus').textContent = 'API key storage could not be read. Reload this page to unlock again; saved keys are preserved.';
    }
    render();
  }
  window.vaultRefresh = () => { if (!pending) refresh(); };
  window.addEventListener('focus', window.vaultRefresh);
  async function expose(key, use) {
    if (pending) return;
    epoch++; hideSecrets(); pending = true; render();
    let result;
    try {
      result = await api('/api/credentials/reveal', {id:key.id});
      if (!document.hidden) await use(result.api_key);
    } catch (error) { await refresh(); feedback(error.message, true); }
    finally { if (result) result.api_key = ''; result = null; pending = false; render(); }
  }
  async function act(action, supplied) {
    if (pending) return;
    let payload = supplied || (action === 'create' ? {password:byId('vaultPassword').value,
      confirmation:byId('vaultConfirm').value}
      : action === 'unlock' ? {password:byId('vaultPassword').value} : {});
    if (!validate(action, payload)) return;
    epoch++; clear(); hideSecrets(); pending = true; render();
    feedback('Updating saved API keys…');
    try {
      const result = await api('/api/credentials/' + action, payload);
      editor = null; setup = false; resetReview = null; byId('vaultResetConfirmDelete').checked = false; byId('vaultName').value = '';
      accept(result); feedback(action === 'create' ? 'API management password created. You can now add API keys.' : action === 'reset' ? 'Password reset. All previously saved keys were deleted. Add your keys again, then choose them again in any affected model connections.' : describe());
      if (action === 'unlock' && addAfterUnlock) { addAfterUnlock = false; begin('add'); feedback('Unlocked. Enter the label and API key below.'); }
      if (action === 'lock' || action === 'reset') addAfterUnlock = false;
    } catch (error) {
      await refresh(); feedback(error.message, true);
    } finally { payload = null; pending = false; render(); }
  }
  byId('vaultSetup').onclick = () => { setup = true; clear(); render(); byId('vaultPassword').focus(); };
  byId('vaultSetupCancel').onclick = () => { setup = false; clear(); render(); feedback(describe()); };
  byId('vaultReset').onclick = async () => {
    if (pending) return;
    await refresh();
    if (!state?.configured || !state.revision) return;
    resetReview = state.revision; editor = null; clear(); hideSecrets();
    byId('vaultResetConfirmDelete').checked = false;
    byId('vaultResetUsage').textContent = !state.usage_known ? 'Connection usage could not be checked. Any connection using a saved key will need a new key choice.'
      : state.vault_deployments.length ? `Affected model connections: ${state.vault_deployments.join(', ')}.` : 'No saved connection uses these keys.';
    render(); byId('vaultResetPassword').focus();
  };
  byId('vaultResetConfirmDelete').onchange = render;
  byId('vaultResetCancel').onclick = () => { resetReview = null; clear(); byId('vaultResetConfirmDelete').checked = false; render(); feedback(describe()); };
  byId('vaultResetApply').onclick = () => {
    if (!resetReview || !byId('vaultResetConfirmDelete').checked) return;
    act('reset', {password:byId('vaultResetPassword').value, confirmation:byId('vaultResetConfirm').value,
      revision:resetReview, acknowledgement:'DELETE_ALL_SAVED_API_KEYS'});
  };
  byId('vaultCreate').onclick = () => act('create');
  byId('vaultUnlock').onclick = () => act('unlock');
  byId('vaultLock').onclick = () => act('lock');
  byId('vaultAdd').onclick = () => {
    if (state?.unlocked) { begin('add'); return; }
    addAfterUnlock = true;
    feedback('Unlock with your API management password above to add a key.');
    byId('vaultPassword').focus();
  };
  byId('vaultCancel').onclick = () => { editor = null; clear(); byId('vaultName').value = ''; render(); };
  byId('vaultSave').onclick = () => {
    if (!editor) return;
    const payload = editor.action === 'add' ? {label:byId('vaultName').value, provider:byId('vaultProvider').value, api_key:byId('vaultKey').value}
      : editor.action === 'rename' ? {id:editor.id, label:byId('vaultName').value} : {id:editor.id, api_key:byId('vaultKey').value};
    act(editor.action, payload);
  };
  // Enter does what the visible primary button does (GUI review S9); there is no form element to submit by accident.
  const enter = (ids, target) => ids.forEach(id => byId(id).addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.isComposing) return;
    const button = target();
    if (!button || button.hidden || button.disabled) return;
    event.preventDefault(); button.click();
  }));
  enter(['vaultPassword', 'vaultConfirm'], () => byId(byId('vaultCreate').hidden ? 'vaultUnlock' : 'vaultCreate'));
  enter(['vaultResetPassword', 'vaultResetConfirm'], () => byId('vaultResetApply'));
  enter(['vaultName', 'vaultKey'], () => byId('vaultSave'));
  window.addEventListener('pagehide', () => { clear(); hideSecrets(); });
  // Switching to a provider tab or password manager is part of entering a key.
  // Preserve unfinished masked inputs; only hide explicitly revealed saved keys.
  document.addEventListener('visibilitychange', () => { if (document.hidden) hideSecrets(); });
  refresh();
})();
