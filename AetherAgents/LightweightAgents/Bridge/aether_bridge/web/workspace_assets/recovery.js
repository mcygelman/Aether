/* Owner: Settings → Backup & recovery for Admins: coverage preview, offline backup steps and the staged-restore report.
 * Must never: run a backup, restore or switch from the page, read or ask for API key values, or claim that an inventory,
 * a check or a staged copy is a completed backup or an active installation. The server enforces Admin access.
 */
import {$, announce, api, button, can, card, el, heading, link, overview, workspaceLink} from './kit.js';
const API='/api/maintenance/';
const STATUS={'included':['Included','good'],'not-yet-created':['Nothing saved yet',''],'outside':['Outside the plan','warning'],
 'missing':['Not found','warning'],'excluded':['Left out','']};
const PLAN={'READY_TO_PLAN':'Ready to plan','NEEDS_ATTENTION':'Needs attention','REFUSED':'Cannot plan yet'};
const PROVIDERS=[['external-key-provider','Provider account or password manager'],['windows-credential-manager','Windows Credential Manager'],['environment','Environment variable'],['file-provider','Key file kept elsewhere']];
const size=n=>n>=1073741824?(n/1073741824).toFixed(1)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB';
function pill([text,cls]){return el('span',{class:'pill '+cls,text});}
function copyable(command){
 const box=el('div',{},el('pre',{},el('code',{text:command})));
 box.append(button('Copy command',async()=>{try{await navigator.clipboard.writeText(command);announce('Command copied.');}catch{announce('Copy is not available here. Select the command text and copy it.',true);}}));
 return box;
}
function steps(rows){
 const list=el('ol',{class:'recovery-steps'});
 for(const s of rows){const item=el('li',{},el('strong',{text:s.title}),el('p',{class:'small',text:s.note||s.text||''}));if(s.supported===false)item.prepend(pill(['Not available','warning']));if(s.command)item.append(copyable(s.command));list.append(item);}
 return list;
}
function categories(data){
 const grid=el('div',{class:'grid'});
 for(const c of data.categories){
  const box=card(c.label,c.purpose),list=el('ul',{class:'recovery-locations'});
  for(const row of c.locations){const item=el('li',{},el('div',{},pill(STATUS[row.status]||[row.status,''])),el('span',{class:'code',text:row.path}));if(row.note)item.append(el('span',{class:'small',text:row.note}));if(row.action)item.append(el('span',{class:'small',text:row.action}));list.append(item);}
  if(c.locations.length)box.append(list);if(c.note)box.append(el('p',{class:'small muted',text:c.note}));grid.append(box);
 }
 return grid;
}
function dependencies(){
 const rows=[],box=card('Credential recovery list (optional)','Backups never contain API keys or the key store. Name where each key can be recovered; this list holds labels only. Never type a key here.');
 const form=el('div',{class:'recovery-dependency'}),provider=el('select',{'aria-label':'Where the key is kept'}),reference=el('input',{type:'text','aria-label':'Reference',placeholder:'Account or entry name',maxlength:160}),purpose=el('input',{type:'text','aria-label':'Purpose',placeholder:'What it unlocks',maxlength:160});
 for(const [id,text]of PROVIDERS)provider.append(el('option',{value:id,text}));
 const listed=el('ul',{class:'recovery-locations'});
 const add=button('Add to list',()=>{const r=reference.value.trim(),p=purpose.value.trim();if(!r||!p){announce('Enter a reference and a purpose.',true);return;}if(/[\r\n]/.test(r+p)){announce('Use one line for each field.',true);return;}rows.push({provider:provider.value,reference:r,purpose:p});listed.append(el('li',{},el('span',{text:provider.selectedOptions[0].text+' · '+r+' · '+p})));reference.value=purpose.value='';});
 const save=button('Save list as a file',()=>{if(!rows.length){announce('Add at least one entry first.',true);return;}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(rows,null,2)+'\n'],{type:'application/json'}));a.download='credential-dependencies.json';document.body.append(a);a.click();a.remove();announce('Saved. Enter the saved file’s full path below.');});
 form.append(el('label',{},el('span',{class:'small',text:'Where the key is kept'}),provider),el('label',{},el('span',{class:'small',text:'Reference'}),reference),el('label',{},el('span',{class:'small',text:'Purpose'}),purpose),add);
 box.append(form,listed,el('div',{class:'actions'},save));
 return box;
}
function planner(data){
 const box=card('Prepare the backup commands','Type full folder paths. Each new folder must not exist yet. The page checks the paths and writes the exact commands; it creates nothing.');
 const field=(label,hint,value='')=>{const input=el('input',{type:'text',value,spellcheck:'false',autocomplete:'off'});return [input,el('label',{},el('span',{text:label}),input,el('span',{class:'small muted',text:hint}))];};
 const [destination,d1]=field('Backup folder','A new folder on another drive, a USB disk or a synced folder, for example E:\\Aether backups\\2026-10-27.');
 const [plan,p1]=field('Plan file','A new file outside the backup folder, for example E:\\Aether backups\\2026-10-27-plan.json.');
 const [staging,s1]=field('Staging folder for a later restore (optional)','Adds restore and report commands. Restoring never replaces this installation.');
 const [deps,c1]=field('Credential recovery list file (optional)','The file saved from the credential recovery list.');
 const form=el('div',{class:'recovery-form'},d1,p1);
 const mounts=Object.entries(data.suggested_mounts||{}),checks=[];
 if(mounts.length){const group=el('fieldset',{},el('legend',{text:'Folders outside the plan'}));for(const [id,path]of mounts){const c=el('input',{type:'checkbox',checked:true,value:id});checks.push([c,path]);group.append(el('label',{class:'recovery-check'},c,el('span',{class:'code',text:path})));}group.append(el('p',{class:'small muted',text:'Checked folders are added with --mount. Leaving one unchecked leaves its data out of the backup.'}));form.append(group);}
 const extra=el('textarea',{'aria-label':'Other folders to include',spellcheck:'false',placeholder:'One full folder path per line'});
 form.append(el('label',{},el('span',{text:'Other folders to include (optional)'}),extra,el('span',{class:'small muted',text:'Only folders you name are added. Aether never searches your disks.'})),s1,c1);
 const result=el('div',{});
 plan.placeholder='Leave empty to use the backup folder name followed by -plan.json';
 const check=button('Check folders and show commands',async()=>{
  if(!plan.value.trim()&&destination.value.trim())plan.value=destination.value.trim().replace(/[\\/]+$/,'')+'-plan.json';
  const chosen={};checks.forEach(([c,path])=>{if(c.checked)chosen[c.value]=path;});
  extra.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean).forEach((path,i)=>{chosen['extra-own-'+(i+1)]=path;});
  try{const r=await api(API+'check',{destination:destination.value,plan:plan.value,mounts:chosen,staging:staging.value,dependencies:deps.value});
   result.replaceChildren();
   if(!r.ok){result.append(el('h3',{text:'Fix these first'}),el('ul',{class:'recovery-problems'},...r.problems.map(p=>el('li',{text:p}))));return;}
   if(r.warnings.length)result.append(el('ul',{},...r.warnings.map(p=>el('li',{text:p}))));
   result.append(el('p',{class:'banner',text:'Run these in Command Prompt (cmd.exe), one at a time, after Mission Control is closed. Each step prints a result; continue only when it succeeds.'}),steps(r.steps));
  }catch(e){announce(e.message,true);}
 },'primary');
 box.append(form,el('div',{class:'actions'},check),result);
 return box;
}
function table(head,rows){return el('div',{class:'recovery-scroll'},el('table',{class:'recovery-table'},el('thead',{},el('tr',{},...head.map(h=>el('th',{text:h})))),el('tbody',{},...rows.map(r=>el('tr',{},...r.map(v=>el('td',{text:v??'—'})))))));}
function report(r){
 const out=el('div',{});
 const staged=r.status==='VERIFIED_STAGING_NOT_ACTIVE';
 out.append(el('p',{},pill(staged?['Verified staging — not active','good']:[r.active?'Switched into the original folders':'Held for reconciliation','warning'])),el('p',{text:r.summary}));
 if(!staged)return out;
 if(!r.same_installation)out.append(el('p',{class:'banner',text:'This backup was made by a different installation folder layout. Return it only to the installation that made it.'}));
 out.append(el('h3',{text:'Restored folders'}),table(['Root','Original folder','Staged copy','Original present'],r.roots.map(x=>[x.id,x.original,x.staged,x.original_present?'Yes':'No'])));
 const kinds={restored:'Names an original folder; correct again after switch',external:'Outside the backup; must exist at that path',application:'Application folder (same build)',credentials:'Credential folder; never in a backup'};
 const counts=Object.entries(kinds).map(([k,text])=>[r.references.filter(x=>x.kind===k).length,text]).filter(([n])=>n).map(([n,text])=>n+' · '+text);
 out.append(el('h3',{text:'Saved references to folders'}),el('p',{class:'small',text:r.references.length+' found'+(r.references_truncated?' (list shortened)':'')+'. Nothing was rewritten.'}),el('ul',{},...counts.map(t=>el('li',{text:t}))),
  el('details',{},el('summary',{text:'Show every reference'}),table(['File','Field','Folder','Meaning'],r.references.map(x=>[x.file,x.field,x.path,kinds[x.kind]]))));
 if(r.receipts.length)out.append(el('h3',{text:'Specialist creation receipts'}),table(['Receipt','State','On retry'],r.receipts.map(x=>[x.file,x.complete?'Complete':'Incomplete',x.retry])));
 if(r.accounts)out.append(el('h3',{text:'Accounts and key grants'}),el('p',{text:r.accounts.accounts+' accounts ('+r.accounts.admins+' Admin), roles: '+(r.accounts.roles.join(', ')||'none')+'. '+r.accounts.note}),table(['Account','Granted key IDs'],r.accounts.key_grants.map(g=>[g.account,g.keys.join(', ')])));
 if(r.sources)out.append(el('h3',{text:'Data sources'}),el('p',{text:r.sources.reports+' saved reports. '+r.sources.note}),table(['Source','Alias','Connection','Token'],r.sources.sources.map(s=>[s.name,s.alias,s.transport,s.token==='NOT_IN_BACKUP'?'Left out: re-enter':s.token==='WINDOWS_PROTECTED'?'Protected for this Windows account':'None'])));
 if(r.models)out.append(el('h3',{text:'Model connections'}),table(['Connection','Model','Key name'],r.models.connections.map(m=>[m.name,m.model,m.key])));
 out.append(el('h3',{text:'Memory owners and seals'}),table(['Cartridge','Owner','Owner verified','Seal'],r.cartridges.map(c=>[c.file,c.owner,c.owner_matches?'Yes':'No',c.seal+(c.seal_note?' — '+c.seal_note:'')])));
 if(r.obligations.length)out.append(el('p',{class:'banner',text:r.obligations.length+' unfinished obligations were preserved, not released. Nothing is retried or refunded.'}));
 out.append(el('h3',{text:'What you can do next'}),steps(r.steps));
 return out;
}
function reader(){
 const box=card('Read a restored copy','After Maintenance.cmd restore, enter the staging folder. The report verifies it again and lists what reconnecting would need. Reading changes nothing.');
 const input=el('input',{type:'text',spellcheck:'false',autocomplete:'off','aria-label':'Staging folder'}),result=el('div',{});
 const read=button('Read the report',async()=>{try{result.replaceChildren(report(await api(API+'recovery-report',{target:input.value})));}catch(e){result.replaceChildren();announce(e.message,true);}},'primary');
 box.append(el('div',{class:'recovery-form'},el('label',{},el('span',{text:'Staging folder'}),input)),el('div',{class:'actions'},read),result);
 return box;
}
export async function recoveryPage(){
 if(!document.getElementById('recovery-style'))document.head.append(el('link',{id:'recovery-style',rel:'stylesheet',href:'/workspace-assets/recovery.css'}));
 heading('Backup & Recovery','See what a backup keeps, prepare the offline backup steps and read a restored copy before using it.',[link('Storage & Folders',workspaceLink('storage'))]);
 const root=$('page-content');root.classList.add('recovery-view');
 if(!can('admin')){root.append(card('Admins only','Backup coverage lists private folders, so only an Admin can see it. Ask an Admin to make or check backups.'));return;}
 const data=await api(API+'coverage');
 if(data.status==='NOT_INSTALLED'){root.append(card('Not an installed copy',data.message));return;}
 root.append(overview([[PLAN[data.status]||data.status,'Coverage'],[data.files,'Files in scope'],[size(data.bytes),'To save'],[Object.keys(data.roots).length,'Folders in the plan']]));
 root.append(el('p',{class:'banner',text:'This is a preview, not a backup. A backup exists only after the offline steps below finish and verify.'}));
 if(data.refusal)root.append(el('section',{class:'ui-card'},el('h2',{text:'A backup cannot be planned yet'}),el('p',{class:'recovery-problems',text:data.refusal})));
 const before=card('Before each backup','Backups run while Mission Control is closed, so nothing changes during the copy. Locked or changing data is refused, never forced.');
 before.append(el('ol',{},...['Let active turns and reports finish.','Leave each Aether network (Aether Networks).','Close Mission Control: close its browser tab and the window that runs Aether.cmd.','Close other programs that edit files in the folders below.'].map(t=>el('li',{text:t}))));
 root.append(before,categories(data),dependencies(),planner(data),reader());
}
