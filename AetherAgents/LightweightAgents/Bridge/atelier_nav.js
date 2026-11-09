/* Owner: shared navigation links, project tabs and legacy synchronous navigation API.
 * Must never: own theme rules, help copy, readiness rendering or area features.
 */
(() => {
  const here = location.pathname, query = new URLSearchParams(location.search);
  const ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
  const legacySurface = here === '/agents' ? 'agents' : here.startsWith('/settings/') ? 'settings' :
    here === '/projects/roster' ? 'roster' : here === '/network' ? 'network' :
    here === '/projects/atelier' ? 'project' : here.startsWith('/projects/campaign') ? 'records' : '';
  if (legacySurface) document.documentElement.dataset.aetherSurface = legacySurface;

  const page = (view) => view === 'roadmaps' ? '/roadmaps' : '/workspace?view=' + encodeURIComponent(view);
  const helpPage = topic => '/workspace?view=help&topic=' + encodeURIComponent(topic);
  const settingsLinks=[['Overview',page('settings')],['API Keys','/settings/api-keys/'],['Models','/settings/models-context/'],['Storage & Folders',page('storage')],['Backup & Recovery',page('recovery')],['Aether Networks','/network'],['Roles',page('roles')],['Security',page('security')],['Agents','/agents?from=settings'],['Emergency Halt',page('halt')]];
  const SECTIONS = [
    ['Chat', '/', []],
    ['Missions', null, [['Add New', '/missions?new=1'], ['Manage', '/missions']]],
    ['Roadmaps', page('roadmaps'), []],
    ['Active Sprints', page('sprints'), []],
    ['Staff', null, [['Human', page('humans')], ['AI', '/agents'], ['Create a specialist', '/workspace?view=create-specialist']]],
    ['Teach', page('teach'), []],
    ['Creations', page('creations'), []],
    ['Dashboards', null, [['My Chat',page('chat-home')]].concat(['Missions','Roadmaps','Sprints','AI Staff','Teach Frames','Creations'].map(x => [x, page('dashboard') + '&area=' + encodeURIComponent(x)])).concat([['Agent notes to self', '/agents/insights'], ['Memory viewer', '/agents/insights?view=memory']])],
    ['Settings', null, settingsLinks],
    ['Help', null, ['About','Guides','Chat','Missions','Roadmaps','Sprints','Operator Staff','AI Staff',
      'Teach Frames','Creations','Dashboards','Settings','Log Bug','View Logs','View Lexicon'].map(x => [x, helpPage(x)])]];
  const TABS = [['Conversation', '/projects/atelier?commission='], ['Roadmap', '/projects/campaign?id='],
                ['Team', '/projects/roster?commission='], ['Records', '/projects/campaign-receipts?id=']];
  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = '/workspace-assets/theme.css';

  document.head.append(style);

  // The bar spans the window and its content sits in the same centred column on every page, whatever the page's own
  // body width or margins (the links must not jump between pages).
  const nav = document.createElement('nav');
  nav.id = 'aether-nav';
  nav.setAttribute('aria-label', 'Aether');
  const row = document.createElement('div');
  row.className = 'ae-row';
  nav.append(row);
  const brand = document.createElement('span');
  brand.className = 'ae-brand';
  brand.setAttribute('aria-label', 'Aether Mission Control');
  brand.textContent = 'Aether Mission Control';
  const heading = document.createElement('div');
  heading.className = 'ae-heading';
  const local = document.createElement('a');
  local.textContent = 'Local operator'; local.href = page('humans');
  const appearance = document.createElement('button');
  appearance.id = 'aether-theme-toggle';
  appearance.type = 'button';
  const utilities = document.createElement('div');
  utilities.className = 'ae-utilities';
  utilities.append(local, appearance);
  heading.append(brand, utilities);
  window.aetherShellParts.theme(appearance);
  nav.prepend(heading);
  const accessScript = document.createElement('script');
  accessScript.src = '/workspace-assets/access.js';
  function link(label, href) {
    const a = document.createElement('a');
    a.textContent = label; a.href = href;
    const target = new URL(href, location.href);
    if ((here === target.pathname && (target.searchParams.get('view') === query.get('view') && target.searchParams.get('area') === query.get('area') && target.searchParams.get('topic') === query.get('topic'))) ||
        (href === '/' && here === '/chat')) a.setAttribute('aria-current', 'page');
    return a;
  }
  for (const [label, href, children] of SECTIONS) {
    if (href) { row.append(link(label, href)); continue; }
    const menu = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = label;
    const panel = document.createElement('div');
    panel.className = 'ae-menu';
    for (const [title, url] of children) panel.append(link(title, url));
    if (panel.querySelector('[aria-current]')) summary.classList.add('ae-active');
    menu.append(summary, panel); row.append(menu);
    menu.addEventListener('toggle', () => {
      if (menu.open) for (const other of row.querySelectorAll('details')) if (other !== menu) other.open = false;
    });
  }
  document.addEventListener('click', e => {
    if (!nav.contains(e.target)) for (const menu of row.querySelectorAll('details')) menu.open = false;
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') for (const menu of row.querySelectorAll('details[open]')) {
      menu.open = false; menu.querySelector('summary').focus();
    }
  });
  const chips = document.createElement('span');
  chips.id = 'aether-chips';
  chips.setAttribute('aria-label', 'Readiness');
  // Existing work pages retain network/key status. Model-only Chat has no network dependency.
  if (here !== '/' && here !== '/chat' && here !== '/login') nav.append(chips);

  // Shared appearance palette. Full-edition pages retain their own styling.


  const tabs = document.createElement('nav');
  tabs.id = 'aether-tabs';
  tabs.hidden = true;
  const tabRow = document.createElement('div');
  tabRow.className = 'ae-row';
  tabs.append(tabRow);
  const note = document.createElement('div');
  note.id = 'aether-note';
  note.setAttribute('role', 'status');
  function fit() {
    const body = document.body;
    if (!body) return;
    const box = body.getBoundingClientRect(), css = getComputedStyle(body);
    const left = box.left + parseFloat(css.paddingLeft || 0) + parseFloat(css.borderLeftWidth || 0);
    for (const bar of [nav, tabs]) {
      bar.style.marginLeft = -left + 'px';
      bar.style.width = document.documentElement.clientWidth + 'px';
    }
  }
  const place = () => {
    if (!document.body) return false;
    document.body.prepend(nav, tabs);
    // Account controls query this shell; load them only after it is in the DOM.
    if (!accessScript.isConnected) document.head.append(accessScript);
    fit();
    return true;
  };
  if (!place()) document.addEventListener('DOMContentLoaded', place);
  function settingsNavigation(){
    if(!(here.startsWith('/settings/')||here==='/network'||here==='/agents'&&query.get('from')==='settings'||here==='/workspace'&&['settings','storage','recovery','roles','security','halt'].includes(query.get('view'))))return;
    const main=document.querySelector('main');if(!main||document.getElementById('aether-settings-nav'))return;
    const section=document.createElement('nav');section.id='aether-settings-nav';section.setAttribute('aria-label','Settings pages');
    for(const [label,href]of settingsLinks){const a=link(label,href);if(label==='Models'&&here.startsWith('/settings/models-context/'))a.setAttribute('aria-current','page');section.append(a);}main.prepend(section);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',settingsNavigation);else settingsNavigation();
  window.addEventListener('resize', fit);
  window.addEventListener('load', () => { fit(); attachHelp(); });

  // Help in place (a "?" on each area, not a checklist). Pages mark an area with data-help="<key>", or
  // the map below names a heading on a page this script does not own.
  const {attach:attachHelp,add:help}=window.aetherShellParts.help(here);

  let current = null, catalogue = null;
  async function campaignsList(fresh) {
    if (!catalogue || fresh) {
      try { const r = await fetch('/api/campaigns', {cache: 'no-store'}); catalogue = r.ok ? await r.json() : {campaigns: [], unfiled: []}; }
      catch (_) { catalogue = {campaigns: [], unfiled: []}; }
    }
    return catalogue;
  }
  async function placeOf(id) {
    const c = await campaignsList();
    for (const campaign of c.campaigns || []) {
      const p = (campaign.projects || []).find(x => x.id === id);
      if (p) return {campaign, project: p};
    }
    return {campaign: null, project: (c.unfiled || []).find(x => x.id === id) || null};
  }
  function project(id) {
    tabRow.replaceChildren();
    const next = id && ID.test(id) ? id : null, changed = next !== current;
    current = next;
    if (changed && started) refresh();
    if (!current) { tabs.hidden = true; return; }
    const crumbs = document.createElement('span');
    crumbs.className = 'ae-crumbs';
    const top = document.createElement('a');
    top.href = '/missions'; top.textContent = 'Missions';
    const name = document.createElement('span');
    name.className = 'ae-title'; name.textContent = id;
    const sep = () => { const s = document.createElement('span'); s.className = 'ae-sep'; s.textContent = '›'; return s; };
    crumbs.append(top, sep(), name);
    tabRow.append(crumbs);
    placeOf(id).then(({campaign, project: p}) => {
      if (p) { name.textContent = p.name; name.title = p.name; }
      if (campaign) {
        const a = document.createElement('a');
        a.href = '/missions#' + encodeURIComponent(campaign.id); a.textContent = campaign.name;
        crumbs.insertBefore(sep(), name); crumbs.insertBefore(a, name.previousSibling);
      }
    });
    const list = document.createElement('span');
    list.className = 'ae-tablist';
    for (const [tab, prefix] of TABS) {
      const a = document.createElement('a');
      a.textContent = tab; a.href = prefix + encodeURIComponent(id);
      if (here === prefix.split('?')[0]) a.setAttribute('aria-current', 'page');
      list.append(a);
    }
    tabRow.append(list, note);
    tabs.hidden = false;
  }
  function agents() {
    const list = document.createElement('span');
    list.className = 'ae-tablist';
    list.style.marginLeft = '0';
    for (const [tab, href] of [['Availability', '/agents'], ['Create a specialist', '/workspace?view=create-specialist'], ['Notes to Self', '/agents/insights'], ['Memory viewer', '/agents/insights?view=memory']]) {
      const a = document.createElement('a');
      a.textContent = tab; a.href = href;
      const target = new URL(href, location.origin);
      if (here === target.pathname && query.get('view') === target.searchParams.get('view')) a.setAttribute('aria-current', 'page');
      list.append(a);
    }
    tabRow.append(list);
    tabs.hidden = false;
  }
  // Readiness (GUI review S4): two chips and a note on a project with no team or a computer outside any network.
  // Information only: nothing here disables or holds anything.
  const {refresh}=window.aetherShellParts.readiness({chips,note,fit,getCurrent:()=>current});
  let started = false;
  if (here.startsWith('/agents') || here.startsWith('/agent-editor') || (here === '/workspace' && query.get('view') === 'create-specialist')) agents();
  else if (here !== '/projects/tasker') project(query.get('commission') || query.get('project') || (here.startsWith('/projects/campaign') ? query.get('id') : null));
  // Pages that switch project without reloading tell the navigation (project.html, campaign_roster.html).
  window.aetherNav = {campaign: project, project, refresh, campaigns: campaignsList};
  window.aetherHelp = {attach: attachHelp, add: help};
  started = true;
  if (here !== '/' && here !== '/chat' && here !== '/login') {
    refresh();
    setInterval(refresh, 20000);
    window.addEventListener('focus', refresh);
  }
  if (document.readyState !== 'loading') attachHelp();
  else document.addEventListener('DOMContentLoaded', () => attachHelp());
})();
