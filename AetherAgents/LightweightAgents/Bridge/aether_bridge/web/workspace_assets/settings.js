/* Owner: workspace Settings overview, setup guidance, Storage & Folders, Security and Emergency Halt.
 * Must never: move data, install inference engines, change folder policy, or implement another area.
 */
import {$, action, announce, api, button, can, card, count, download, editor, el, heading, link, mutate, operator, overview, params, store, workspaceLink} from './kit.js';
import {projects} from './project-data.js';
function gettingStarted(models,catalogue){
 const identity=store.installation,projectRows=catalogue?.rows.filter(p=>p.kind!=='library'&&p.kind!=='chat'),creations=store.artifacts.filter(a=>!a.archived);
 const milestones=[
  {title:'Name this installation',done:!!identity,status:identity?'Named':'Not named yet',text:identity?identity.name:'Give operators a recognizable team, purpose or location name.',label:'Installation details',href:'#installation-settings',hint:can('manage_settings')?'Rename it at any time; its ID stays the same.':'An Admin or someone with Manage Settings can name it.'},
  {title:'Connect a model',done:models===null?null:models.length>0,status:models===null?'Status unavailable':models.length?'Connection saved':'No connection saved',text:'Use this installation’s computer, a server on your network or a hosted provider.',label:'Set up compute',href:'/settings/models-context/',hint:'A saved connection is not a live check. Open Models to check the server; add a key only if it requires one.'},
  {title:'Organize a project',done:projectRows===undefined?null:projectRows.length>0,status:projectRows===undefined?'Status unavailable':projectRows.length?'Project recorded':'No project recorded',text:'Add agents (Staff → AI → Create starter team), join this computer’s network (Settings → Aether Networks) so they can take turns, then open a project in Missions and add its team.',label:'Open Missions & projects',href:'/missions',hint:'Projects are optional for Chat. A brief with no budget gets a default of 60 model calls; joining calls no model.'},
  {title:'Keep a useful result',done:creations.length>0,status:creations.length?'Creation saved':'No current creation',text:'Create or import a note, HTML page, SVG or other result in Creations. Add a project link when useful.',label:'Open the showcase',href:workspaceLink('creations'),hint:can('admin')?'Save it here when it is appropriate to share with this installation’s operators.':'An Admin can add Creations; Manage Teach also allows Teach Frames. You can view and export saved results.'}
 ];
 const recorded=milestones.filter(m=>m.done===true).length,unavailable=milestones.filter(m=>m.done===null).length;
 const box=el('details',{id:'getting-started',class:'ui-card getting-started',open:params.get('setup')==='1'||recorded<milestones.length});
 box.append(el('summary',{text:'Getting started · '+recorded+' of '+milestones.length+' milestones recorded'+(unavailable?' · '+unavailable+' unavailable':'')}),el('p',{class:'muted',text:'A suggested path through Mission Control. Progress reflects saved records in this installation; it does not certify model readiness or completed work.'}));
 const steps=el('ol',{class:'grid setup-steps'});
 milestones.forEach((m,i)=>{const row=el('li',{'data-step':i+1},el('div',{class:'row'},el('h3',{text:(i+1)+'. '+m.title}),el('span',{class:'pill '+(m.done===true?'good':m.done===null?'warning':''),text:m.status})),el('p',{text:m.text}),link(m.label,m.href),el('p',{class:'small',text:m.hint}));steps.append(row);});
 box.append(steps,el('p',{class:'small',text:'Chat works without a project. Private Chat results count here only after you deliberately save a workspace copy. API keys, agent setup and Aether network membership depend on how you choose to work.'}),el('div',{class:'actions'},link('Try Chat','/'),link('Read operator guides',workspaceLink('help','&topic=Guides')),link('Review operator roles',workspaceLink('roles'))));
 return box;
}
async function storagePage(){
 heading('Storage & Folders','See where this installation keeps its work and open the controls for each location.');
 const root=$('page-content'),data=await api('/api/workspace/storage'),grid=el('div',{class:'grid'});
 for(const row of data.locations){const item=card(row.label,row.purpose);for(const path of row.paths)item.append(el('p',{class:'code',text:path}));if(!row.paths.length)item.append(el('p',{class:'muted',text:'Not configured'}));if(row.href)item.append(link('Manage '+row.label.toLowerCase(),row.href));grid.append(item);}
 root.append(grid,card('Choosing locations','Use the linked page to change a supported setting. Chat file access belongs to each individual Chat. Installation data and credential roots are selected by the installer; moving live stores is not available here.'));
 const backup=card('Back up this installation','Use Maintenance.cmd in the installed application folder. Leave each Aether network and allow active turns to finish, then close Mission Control before making a backup.');
 backup.append(el('p',{text:'The maintenance workflow prepares a backup plan, creates the backup, verifies it, and can restore to a new root. Include external network folders and agent homes/cartridges in the plan; they may be outside the data root.'}),el('p',{class:'small',text:'See PUBLIC_SETUP.md → Backup and new-root restore in the release documentation for the commands. Do not copy an open memory cartridge by hand.'}));
 backup.append(can('admin')?link('Open Backup & Recovery',workspaceLink('recovery')):el('p',{class:'small',text:'An Admin can see what a backup includes and prepare its exact commands in Backup & Recovery.'}));root.append(backup);
}
async function settingsPage(){
 heading('Settings','Manage this installation’s connections, access and local resources.');
 const root=$('page-content'),answers=await Promise.allSettled([api('/settings/models-context/api/credentials'),api('/settings/models-context/api/settings'),api('/api/places'),api('/api/agent-availability'),projects()]);
 const value=i=>answers[i].status==='fulfilled'?answers[i].value:null,vault=value(0),settings=value(1),network=value(2),staff=value(3),profiles=settings?.document?.profiles;
 const models=profiles?Object.values(profiles).filter(p=>p.requestedApiIdentifier&&p.requestedApiIdentifier!=='not-configured'):null;
 const vaultState=!vault?'Unavailable':!vault.supported?'Not supported here':!vault.configured?'Not set up':vault.unlocked?'Unlocked':'Locked';
 const identity=store.installation,installation=card('This installation','Give this copy of Mission Control a recognizable name, such as Training Lab or Planning Team.');installation.id='installation-settings';installation.tabIndex=-1;
 installation.append(el('p',{},el('strong',{text:identity?.name||'Not named yet'})));
 if(identity){installation.append(el('p',{class:'small'},document.createTextNode('Installation ID: '),el('code',{text:identity.id,style:'overflow-wrap:anywhere'})),el('p',{class:'small',text:'The ID stays the same when you rename this installation, restart it or update its app. Keep its local data when restoring this installation.'}));}
 const identityActions=el('div',{class:'actions'});
 identityActions.append(button(identity?'Rename installation':'Name installation',()=>{const revision=store.revision;editor(identity?'Rename installation':'Name installation','Choose a name operators can recognize. A persistent installation ID is assigned when you first save.',[{name:'name',label:'Installation name',required:true,max:120,value:identity?.name||'',hint:'Use a short team, purpose or location name.'}],async v=>{await mutate('installation_save',v,revision);window.dispatchEvent(new Event('aether-installation-changed'));},{saveLabel:'Save installation name',readonly:!can('manage_settings')});},'primary','manage_settings'));
 if(identity)identityActions.append(button('Export installation details',()=>download({kind:'aether-installation',...identity},identity.name+'-installation')));
 installation.append(identityActions);root.append(gettingStarted(models,value(4)),installation);
 if(params.get('setup')==='1')root.prepend(el('section',{class:'banner'},el('h2',{text:'Welcome to your installation'}),el('p',{text:'Your first Admin account is ready. Name this installation below, then open Model connections to choose this computer, another computer or a hosted provider. Add an API key only if that server requires one. You can return to Settings at any time.'}),el('div',{class:'actions'},link('Open Chat','/'),link('Open operator guides',workspaceLink('help','&topic=Guides')))));
 root.append(overview([[models?models.length:'—','Saved model connections'],[vaultState,'API key store'],[network?(network.running?'Joined':'Not joined'):'Unavailable','Aether network'],[staff?(staff.agents||[]).filter(a=>a.status==='available').length:'—','Available agents']]));
 const grid=el('div',{class:'grid'}),setting=(title,status,text,href,label)=>{const c=card(title,text);c.prepend(el('div',{class:'eyebrow',text:status}));c.append(el('div',{class:'actions'},link(label,href)));grid.append(c);};
 setting('API Keys',vaultState,!vault?'Open API Keys to inspect this installation’s key store. Its current status could not be read.':!vault.supported?'The local credential store is not supported on this platform. Open API Keys for details.':vault.unlocked?count((vault.keys||[]).length,'saved key')+' in this installation. Manage labels and keys, or lock the store when finished.':vault.configured?'Unlock the key store on the API Keys page when a model needs it. Keys stay separate from connections.':'Create an API management password, then add provider keys on the API Keys page.','/settings/api-keys/','Open API Keys');
 setting('Model connections',models?count(models.length,'saved connection'):'Status unavailable','Add or edit a provider, server and model. Choose the saved connection in Chat or assign it to an agent in a project.','/settings/models-context/','Open Models');
 setting('Data Sources & Reports','MCP services','Connect your organization’s reporting services, assign roles and reuse saved reports. Database accounts and drivers are configured in the MCP service.',workspaceLink('data-sources',can('manage_data_sources')?'&manage=1':''),'Open Data Sources');
 setting('Ultralight helpers',profiles?.ultralight?.requestedApiIdentifier&&profiles.ultralight.requestedApiIdentifier!=='not-configured'?'Connection saved':'Not configured','Save a small-model connection with the exact profile name ultralight to enable Scout, Scribe and Checker helpers for lightweight and heavyweight project agents. Helpers receive selected excerpts, return drafts and share the parent’s call budget. No child tools or persistent memory. Remove that connection to disable future helper turns.','/settings/models-context/','Configure helper connection');
 setting('Storage & Folders','Installation locations','See where work, accounts, conversations and agent records live. Open the existing controls for configurable folders.',workspaceLink('storage'),'Open Storage & Folders');
 setting('Backup & Recovery',can('admin')?'Admins':'Admin access needed','See what a backup keeps or leaves out, prepare the offline backup commands and read a restored copy before using it.',workspaceLink('recovery'),'Open Backup & Recovery');
 setting('Aether Networks',network?(network.running?'This installation is joined':'This installation is not joined'):'Status unavailable','Choose the shared workplace this computer joins and see the computers contributing to it.','/network','Open Aether Networks');
 setting('Operator roles',operator?.role==='admin'?'You are an Admin':'You are a User','Admins manage accounts and access. Users have full Chat and view-only access elsewhere, with optional management permissions.',workspaceLink('roles'),'Open Roles');
 setting('Security',store.halted?'New work is stopped':'New work can be requested','Review the local session and key-store status. Emergency Halt stops new requests; an in-progress request may finish.',workspaceLink('security'),'Open Security');
 setting('Agents',staff?count((staff.agents||[]).length,'registered agent'):'Status unavailable','Set agent folder locations, add existing agents and manage local availability. Project roles and model assignments are managed with the team.','/agents?from=settings','Open Agents');
 root.append(grid,el('div',{class:'banner'},el('strong',{text:'Your account and appearance'}),el('p',{text:'Change your account password here. Your API-vault password is separate. The light/dark toggle remembers your appearance choice on this browser.'}),link('Change password','/login?next='+encodeURIComponent(location.pathname+location.search))));
 if(answers.some(a=>a.status==='rejected'))announce('Some setup status could not be read. The affected cards are marked unavailable; open their pages or Refresh to try again.',true);
 root.append(el('p',{class:'small',text:'This overview reads local setup only. It does not contact a model provider, unlock keys, join a network or start agents. Saved connections are not a live connection check.'}));
}
async function securityPage(){
 heading('Security','Manage the local session, provider credentials and new work.');
 const root=$('page-content'),grid=el('div',{class:'grid'});let vault=null,places=null;
 const answers=await Promise.allSettled([api('/settings/models-context/api/credentials'),api('/api/places')]);
 if(answers[0].status==='fulfilled')vault=answers[0].value;
 if(answers[1].status==='fulfilled')places=answers[1].value;
 const keys=card('API credentials',vault?(vault.configured?(vault.unlocked?'The credential vault is unlocked on this computer.':'The credential vault is locked.'):'Set up the local vault to store provider keys.'):'Credential status could not be read. Open API Keys for details.');
 keys.append(el('div',{class:'actions'},link('Manage API Keys','/settings/api-keys/')));
 if(vault?.unlocked)keys.querySelector('.actions').append(button('Lock API keys',()=>action(()=>api('/settings/models-context/api/credentials/lock',{}),'API keys locked.'),'','manage_settings'));
 const local=card('Local session','Mission Control is served on this computer. Signed-in operators use Admin or User access, with optional management permissions.');local.append(link('Human Staff',workspaceLink('humans')));
 const network=card('Aether network',places?.running?'This installation is joined and can take project turns.':places?'This installation is not taking network turns.':'Open Aether Networks to see this installation’s connection.');network.append(link('Aether Networks','/network'));
 const work=card('New work',store.halted?'New model requests and agent starts are stopped.':'New work can be requested from this workspace.');work.append(link('Emergency Halt',workspaceLink('halt')));
 grid.append(keys,local,network,work);root.append(grid,el('div',{class:'banner',text:'Chat history and authored artifacts are separate from agent memory. Give material to an agent deliberately through its project.'}));
}
function haltPage(){
 heading('Emergency Halt','Stop new work on this installation.');
 const c=card(store.halted?'New work is stopped':'New work is enabled',store.halted?'New Chat requests and agent starts are blocked until you enable requests again.':'Use this control when you need this installation to stop accepting new work.');
 c.append(el('p',{text:'Stopping requests asks the network worker to stop and discards Chat replies still arriving. A provider request already sent or an agent turn already running may finish and be billed.'}));
 c.append(el('div',{class:'actions'},button(store.halted?'Enable new requests':'Stop new work',()=>action(async()=>{const v=await mutate(store.halted?'resume':'halt');if(v.stop_errors?.length)throw Error('New work is stopped, but some running work could not be signalled: '+v.stop_errors.join('; '));},store.halted?'New requests enabled. Join the network again when you want it to take turns.':'New work stopped. Already-running work may still finish.'),store.halted?'primary':'danger','manage_settings'),link('Aether Networks','/network')));
 $('page-content').append(c,el('p',{class:'small',text:'Enabling requests does not automatically rejoin a network or repeat an interrupted model call.'}));
}



export {haltPage, securityPage, settingsPage, storagePage};
