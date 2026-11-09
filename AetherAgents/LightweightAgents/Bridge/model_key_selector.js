/* Model settings select existing credentials; key management has its own page. */
(() => {
  let state = null, sequence = 0;
  window.vaultFillSelector = (select, selected = '') => {
    select.replaceChildren();
    const keys = state?.keys || [];
    select.add(new Option(state?.unlocked ? 'Choose a saved key' : 'Unlock on the API keys page to choose', ''));
    for (const key of keys) select.add(new Option(`${key.label} · ${key.provider === 'deepseek' ? 'DeepSeek' : 'OpenAI-compatible'}`, key.id));
    if (selected && !keys.some(key => key.id === selected))
      select.add(new Option(state?.unlocked ? 'Previously selected key is missing — choose another' : 'Saved selection (locked)', selected));
    select.value = selected;
  };
  async function refresh() {
    const request = ++sequence;
    let value = null;
    try { value = await api('/api/credentials'); } catch (_) { /* Keep saved references when unavailable. */ }
    if (request !== sequence) return;
    state = value;
    for (const id of ['deepseekSavedKey', 'credentialSavedKey']) {
      const select = document.getElementById(id);
      if (select) window.vaultFillSelector(select, select.value);
    }
  }
  window.vaultRefresh = refresh;
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  refresh();
})();
