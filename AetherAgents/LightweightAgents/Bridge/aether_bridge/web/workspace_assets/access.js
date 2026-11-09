/* Owner: account controls and presentation of server-enforced human permissions.
 * Must never: grant server permissions, persist passwords, or own theme rules. */
(()=>{'use strict';

 window.aetherAccess=fetch('/api/access/session',{cache:'no-store'}).then(r=>r.json()).then(info=>{
  const user=info.user,can=p=>!!user&&(user.role==='admin'||user.permissions.includes(p));window.aetherCan=can;
  const account=document.querySelector('.ae-utilities a');if(account){account.textContent=user?user.name+' · '+(user.role==='admin'?'Admin':'User'):'Sign in';account.href='/login';}
  if(!user){if(location.pathname!=='/login')location.replace('/login?next='+encodeURIComponent(location.pathname+location.search));return info;}
  // The default Admin's one-time welcome (Michel, 2026-11-07) comes first, on whatever page they open.
  if(info.welcome&&!(location.pathname==='/workspace'&&new URLSearchParams(location.search).get('view')==='welcome')){location.replace('/workspace?view=welcome');return info;}
  const identity=document.createElement('a');identity.id='installation-label';identity.href='/workspace?view=settings';document.querySelector('.ae-utilities')?.prepend(identity);
  async function installation(){try{const r=await fetch('/api/workspace/installation',{cache:'no-store'});if(!r.ok)throw Error();const v=(await r.json()).installation;identity.textContent=v?.name||(can('manage_settings')?'Name this installation':'Unnamed installation');identity.title=v?.id?'Installation ID: '+v.id:'Installation settings';}catch(_){identity.textContent='Installation settings';identity.title='Installation details unavailable';}}
  installation();window.addEventListener('aether-installation-changed',installation);
  const password=document.createElement('a');password.textContent='Change password';password.href='/login?next='+encodeURIComponent(location.pathname+location.search);document.querySelector('.ae-utilities')?.append(password);
  const out=document.createElement('button');out.type='button';out.textContent='Sign out';out.onclick=async()=>{const r=await fetch('/api/access/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(r.ok)location.assign('/login');};document.querySelector('.ae-utilities')?.append(out);
  const p=location.pathname,view=new URLSearchParams(location.search).get('view');
  let permission=p==='/missions'||p.startsWith('/projects/atelier')||p==='/projects/roster'?'manage_missions':p==='/agents'?'manage_agents':p.startsWith('/settings/api-keys/')?null:p==='/network'||p.startsWith('/settings/')?'manage_settings':view==='help'&&new URLSearchParams(location.search).get('topic')==='Guides'?'manage_guides':view==='teach'?'manage_teach':view==='dashboard'?'manage_dashboards':view==='humans'?'manage_accounts':view==='roles'?'manage_roles':view==='creations'?'admin':view==='security'||view==='settings'?'manage_settings':null;
  if(permission&&!can(permission)){const note=document.createElement('p');note.className='operator-access-note';note.textContent=p==='/agents'&&can('manage_missions')?'Agent registration is view-only. You can manage project assignments.':'View-only access · Chat remains fully available.';document.querySelector('main')?.prepend(note);}
  function apply(){
   const deny=selector=>{for(const n of document.querySelectorAll(selector))n.dataset.accessDenied='true';};
   for(const n of document.querySelectorAll('[data-permission]')){if(!can(n.dataset.permission))n.dataset.accessDenied='true';}
   if(!can('manage_missions')){
    deny('#aether-nav a[href="/missions?new=1"]');
    if(p==='/missions')deny('#new,.mission-card .actions button,.mission-card details.more,.project-menu,a[href*="new=1"],#mission-save,#project-save,#notice button');for(const b of document.querySelectorAll('.empty-state button'))if(b.textContent.includes('first mission'))b.dataset.accessDenied='true';
    if(p==='/projects/atelier')deny('#create-form,#next-actions,#send,#text,#attach,#team-link');
    if(p==='/projects/roster')deny('#assignment,#members button');
   }
   if(p==='/agents'&&!can('manage_agents'))deny('#inject,#starter,#starterPanel,#folderPanel,#reviewPanel,#setFolders,.agent [data-permission="manage_agents"]');
   if((p==='/network'||p==='/projects/places')&&!can('manage_settings'))deny('#join,#leave,#rename,#renameForm,#saveOptions');
   if(p.startsWith('/settings/')&&!p.startsWith('/settings/api-keys/')&&!can('manage_settings'))deny('main form button,main button:not(.aether-help),main input[type=password]');
  }
  apply();new MutationObserver(apply).observe(document.body,{childList:true,subtree:true});
  document.addEventListener('click',e=>{if(e.target.closest('[data-access-denied]')){e.preventDefault();e.stopImmediatePropagation();}},true);
  return info;
 }).catch(()=>({user:null}));
})();
