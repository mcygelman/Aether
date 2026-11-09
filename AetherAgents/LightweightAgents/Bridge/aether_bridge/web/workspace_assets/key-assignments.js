/* Own key assignment controls shared by Human Staff and API Keys.
 * Never handle key secrets, authorize requests, or unlock another session.
 */
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
const epochs=new WeakMap();
async function api(path,body){const r=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error||'Assignment request failed.');return v;}
export async function mountAssignments(root){
 const epoch=(epochs.get(root)||0)+1;epochs.set(root,epoch);
 root.replaceChildren(el('h2','Assigned API keys'));
 const status=el('p');status.setAttribute('role','status');root.append(status);
 try{
  const state=await api('/api/access/key-assignments');
  if(epochs.get(root)!==epoch)return;
  root.append(el('p',state.suspended?'API use is suspended.':'Assigned keys remain usable after restart and while management is locked.'));
  if(!state.can_assign){root.append(el('p',state.keys.map(k=>k.label).join(' · ')||'No keys are assigned to your account. Ask an API assignment manager for access.'));return;}
  if(!state.unlocked){const link=el('a','Unlock API management to assign keys');link.href='/settings/api-keys/';root.append(link);return;}
  const button=(text,run)=>{const b=el('button',text);b.type='button';b.onclick=async()=>{b.disabled=true;try{await run();await mountAssignments(root);}catch(e){status.textContent=e.message;status.className='error';}finally{b.disabled=false;}};return b;};
  root.append(button(state.suspended?'Resume assigned API use':'Suspend API use',()=>api('/api/access/key-assignments',{action:'suspend',revision:state.revision,suspended:!state.suspended})));
  const directory=await api('/api/access/accounts');
  if(epochs.get(root)!==epoch)return;
  if(!state.keys.length){root.append(el('p','Add a saved API key before assigning access.'));return;}
  for(const user of directory.accounts){
   const row=el('section',null,'vault-row');row.append(el('h3',user.name+' ('+user.username+')'),el('p',user.active?'Active account':'Disabled account — assigned keys cannot be used'));
   const checks=[];
   for(const key of state.keys){const label=el('label',null,'key-assignment-option'),check=document.createElement('input');check.type='checkbox';check.value=key.id;check.checked=(state.assignments[user.id]||[]).includes(key.id);label.append(check,document.createTextNode(key.label+' · '+key.provider));row.append(label);checks.push(check);}
   row.append(button('Save keys for '+user.name,()=>api('/api/access/key-assignments',{action:'assign',revision:state.revision,id:user.id,keys:checks.filter(c=>c.checked).map(c=>c.value)})));root.append(row);
  }
 }catch(e){status.textContent=e.message;status.className='error';}
}
const standalone=document.getElementById('key-assignments');
if(standalone){mountAssignments(standalone);window.addEventListener('aether-key-management',()=>mountAssignments(standalone));}
