/* Owner: contextual help disclosure rendering.
 * Must never: own help copy, area workflows or application permissions.
 */
window.aetherShellParts.help=function(here){
const {HELP,PAGE_HELP}=window.aetherShellParts.helpData;
  let helpCount = 0;
  function help(target, key) {
    const words = HELP[key];
    if (!target || !words || target.querySelector(':scope > .aether-help')) return;
    const id = 'aether-help-' + (++helpCount);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'aether-help'; button.textContent = '?';
    button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', id);
    button.setAttribute('aria-label', 'Help: ' + (target.textContent || key).trim());
    const box = document.createElement('div');
    box.className = 'aether-help-text'; box.id = id; box.hidden = true;
    for (const part of words) {
      if (Array.isArray(part)) {
        const ul = document.createElement('ul');
        for (const item of part) { const li = document.createElement('li'); li.textContent = item; ul.append(li); }
        box.append(ul);
      } else { const p = document.createElement('p'); p.textContent = part; box.append(p); }
    }
    button.onclick = () => { box.hidden = !box.hidden; button.setAttribute('aria-expanded', String(!box.hidden)); };
    target.append(button);
    target.after(box);
  }
  function attachHelp(scope) {
    for (const el of (scope || document).querySelectorAll('[data-help]')) help(el, el.dataset.help);
    for (const [selector, key] of PAGE_HELP[here] || PAGE_HELP[here.replace(/\/$/, '')] || []) help(document.querySelector(selector), key);
  }

return {attach:attachHelp,add:help};
};
