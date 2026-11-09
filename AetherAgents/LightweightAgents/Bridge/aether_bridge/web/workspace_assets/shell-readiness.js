/* Owner: advisory readiness chips and project readiness note.
 * Must never: block actions, change permissions or start provider inference.
 */
window.aetherShellParts.readiness=function({chips,note,fit,getCurrent}){
  function chip(text, href, kind) {
    const a = document.createElement('a');
    a.textContent = text; a.href = href; a.className = kind;
    chips.append(a);
  }
  function render(r) {
    chips.replaceChildren();
    const k = r.keys || {}, p = r.place || {};
    if (k.supported) {
      if (k.suspended) chip('API use: suspended', '/settings/api-keys/', 'attention');
      else if ('assigned' in k) chip(k.assigned ? 'API keys: '+k.assigned+' assigned' : 'API keys: none assigned', '/settings/api-keys/', k.assigned?'good':'attention');
      else if (!k.configured) chip('API keys: not set up', '/settings/api-keys/', 'attention');
      else if (!k.unlocked) chip('API keys: locked', '/settings/api-keys/', 'attention');
      else chip('API keys: unlocked', '/settings/api-keys/', 'good');
    }
    chip(p.running ? 'Network: joined' : 'Network: not joined', '/network', p.running ? 'good' : 'attention');
    note.replaceChildren();
    if (getCurrent() && r.note) {
      const a = document.createElement('a');
      a.textContent = r.note.label; a.href = r.note.href;
      note.append(r.note.text, a);
    }
    fit();
  }
  let sequence = 0;
  async function refresh() {
    const request = ++sequence;
    try {
      const r = await fetch('/api/readiness' + (getCurrent() ? '?commission=' + encodeURIComponent(getCurrent()) : ''), {cache: 'no-store'});
      if (r.ok && request === sequence) render(await r.json());
    } catch (_) { /* readiness is advisory; every page works without it */ }
  }
return {refresh};
};
