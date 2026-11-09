/* Owner: saved custom revisions and explicit creation from the public editor.
 * Must never: execute imported content, start work, grant capabilities through prose or rewrite an existing agent.
 */
const API='/api/agent-catalog/custom';
function node(tag,text,attrs={}) {
 const e=document.createElement(tag);if(text)e.textContent=text;
 for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;
}
function button(text,fn){const b=node('button',text,{type:'button',class:'btn btn-secondary'});b.addEventListener('click',fn);return b;}
function field(parent,title,control){const label=node('label',title);label.append(control);parent.append(label);return control;}
async function api(path='',body){
 const r=await fetch(API+path,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
 const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed.');return data;
}
function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));const a=node('a','',{href:url,download:name});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

export async function attachCustomTemplates(app,access){
 const root=document.getElementById('custom-content'),status=document.getElementById('custom-status');
 const key='aether-custom-pending:'+access.user;
 let data,selected=null,opened=null,busy=false,pending=null;
 try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch(_){/* Report storage failure before any mutation below. */}
 const say=t=>status.textContent=t;
 const toolbar=node('div','',{class:'custom-toolbar'}),picker=node('select','',{'aria-label':'Saved custom template'}),versions=node('select','',{'aria-label':'Template revision'});
 const open=button('Open revision',()=>run(async()=>{
   if(!selected)return;if(!access.discard())return;
   app.loadBundle(structuredClone(selected.draft));opened=selected.id;title.value=selected.title;fill(selected.config);
   say('Opened '+selected.title+' · revision '+selected.version+'. Edits are unsaved until Save revision.');
 }));
 const exportDraft=button('Download saved draft',()=>selected&&download(selected.draft,'custom-'+selected.id+'-v'+selected.version+'.json'));
 const exportRevision=button('Download complete revision',()=>selected&&download(selected,'custom-'+selected.id+'-v'+selected.version+'-revision.json'));
 const refresh=button('Refresh collection',()=>run(async()=>{await load();say('Collection refreshed; editor draft retained.');}));
 toolbar.append(picker,versions,open,exportDraft,exportRevision,refresh);root.append(toolbar);
 const detail=node('p',''),mapping=node('details'),mappingTitle=node('summary','What becomes operating guidance?');mapping.append(mappingTitle);root.append(detail,mapping);
 const author=node('fieldset','',{class:'custom-authoring'});author.append(node('legend','Save the current editor draft'));
 const title=field(author,'Template title',node('input','',{type:'text',maxlength:'160',value:'Untitled custom specialist'}));
 const kind=field(author,'Project role',node('select'));for(const k of ['member','lead','reviewer'])kind.append(node('option',k,{value:k}));
 const subject=field(author,'Technical subject',node('input','',{type:'text',maxlength:'160'}));
 const dirs=field(author,'Writable project folders (comma separated)',node('input','',{type:'text',placeholder:'deliverables, reviews'}));
 author.append(node('p','Folders are relative to the assigned project. Use simple lowercase names. Other project files are readable. File writing needs the write_file tool.'));
 const tools=node('div','',{class:'custom-tools'});author.append(tools);
 author.append(node('p','Technical learning and project checkpoints are always available. Personality stays fixed; technical understanding and paths evolve directly without approval gates. Project details belong in project checkpoints, not technical lessons. New cartridges contain orientation only, with no inherited learned or project history.'));
 const save=button('Save revision',()=>saveDraft(false)),saveNew=button('Save as new template',()=>saveDraft(true));author.append(save,saveNew);root.append(author);
 const creation=node('fieldset','',{class:'custom-authoring'});creation.append(node('legend','Create from the selected saved revision'));
 const name=field(creation,'New specialist name',node('input','',{type:'text',maxlength:'160'}));
 const connection=field(creation,'Default model connection',node('select'));
 const create=button('Create specialist',()=>run(async()=>{
   if(!selected)throw Error('Choose a saved revision first.');
   await submit('/install',{request_id:crypto.randomUUID(),id:selected.id,version:selected.version,sha256:selected.sha256,name:name.value,deployment:connection.value});
 }));creation.append(node('p','This creates a unique home and registers it in AI Staff. No model or project starts. The saved role, tools and folders shown above are used; unsaved editor changes are not included.'),create);root.append(creation);
 const retry=button('Retry pending request',()=>run(async()=>{if(pending)await submit(pending.path,pending.body);}));
 const clear=button('Clear pending request',()=>{
   if(!window.confirm('A pending request may already have succeeded. Check the collection and AI Staff before starting another creation. Clear this retry record?'))return;
   sessionStorage.removeItem(key);pending=null;controls();say('Retry record cleared. Existing saved templates and agents are unchanged.');
 });root.append(retry,clear,node('p','Pending requests are retained in this browser tab across reloads so a lost response can be retried safely. Download drafts before leaving the editor.'));
 root.append(node('a','Open AI Staff',{href:'/agents'}),document.createTextNode(' · '),node('a','Technical Learning',{href:'/workspace?view=technical-learning'}));
 function controls(){
   author.disabled=!access.allowed||busy||!!pending;creation.disabled=!access.allowed||busy||!!pending||!selected||!data?.default_folder;
   for(const b of [picker,versions,open,exportDraft,exportRevision])b.disabled=busy||!selected;
   refresh.disabled=busy;retry.disabled=busy||!pending||!access.allowed;clear.disabled=busy||!pending;
   access.setBusy(busy||!!pending);
 }
 async function run(fn){if(busy)return;busy=true;controls();try{await fn();}catch(e){say(e.message+(pending?' Pending request retained; retry it before creating another.':''));}finally{busy=false;controls();}}
 function fill(config){kind.value=config.kind;subject.value=config.technical_subject;dirs.value=config.output_dirs.join(', ');for(const c of tools.querySelectorAll('input'))c.checked=config.tools.includes(c.value);}
 function config(){return {kind:kind.value,technical_subject:subject.value,output_dirs:dirs.value.split(',').map(s=>s.trim()).filter(Boolean),tools:[...tools.querySelectorAll('input:checked')].map(c=>c.value)};}
 async function choose(){
   selected=picker.value?await api('/template?id='+encodeURIComponent(picker.value)+'&version='+versions.value):null;
   detail.textContent=selected?`${selected.title} · revision ${selected.version} · by ${selected.draft.bundle_metadata.author} · ${selected.config.kind} · ${selected.config.technical_subject}. Tools: ${selected.config.tools.join(', ')}. Writable folders: ${selected.config.output_dirs.join(', ')||'none'}. Conversion: ${selected.conversion}.`:'No saved custom templates yet. Complete the draft below, then save it here.';
   name.value=selected?.draft.identity.name||'';controls();
 }
 function revisionOptions(){const row=data.templates.find(r=>r.id===picker.value);versions.replaceChildren();for(let i=row?.version||0;i>0;i--)versions.append(node('option','Revision '+i,{value:String(i)}));}
 picker.onchange=()=>run(async()=>{revisionOptions();await choose();});versions.onchange=()=>run(choose);
 async function load(id,version){
   const previous=id||picker.value;data=await api();picker.replaceChildren();
   for(const t of data.templates)picker.append(node('option',t.title+' (v'+t.version+')',{value:t.id}));
   if(data.templates.some(t=>t.id===previous))picker.value=previous;
   revisionOptions();if(version)versions.value=String(version);await choose();
   connection.replaceChildren(node('option','Configure later',{value:'unconfigured'}));for(const n of data.connections)connection.append(node('option',n,{value:n}));
 }
 async function submit(path,body){
   // Persist before sending; failure to retain a receipt prevents the mutation.
   pending={path,body};sessionStorage.setItem(key,JSON.stringify(pending));controls();
   const result=await api(path,body);sessionStorage.removeItem(key);pending=null;
   if(path==='/save'){
     opened=result.id;await load(result.id,result.version);
     if(JSON.stringify(app.currentBundle)===JSON.stringify(result.draft))app.markSaved('Local revision '+result.version,result.sha256);
     say('Saved '+result.title+' · revision '+result.version+'. No model work started.');
   }else{
     say('Specialist created and registered in AI Staff. No model work started. Home: '+result.agents[0].home);
   }
 }
 async function saveDraft(fresh){await run(async()=>{if(pending)throw Error('Resolve the pending request first.');await submit('/save',{request_id:crypto.randomUUID(),id:fresh?null:opened,revision:data.revision,title:title.value,draft:structuredClone(app.currentBundle),config:config()});});}
 await run(async()=>{
   await load();for(const t of data.tools){const c=node('input','',{type:'checkbox',value:t});const label=node('label',t);label.prepend(c);tools.append(label);}fill(data.defaults);
   for(const [heading,items] of Object.entries(data.mapping)){mapping.append(node('h3',heading==='active'?'Operating guidance':'Retained reference'));const list=node('ul');for(const item of items)list.append(node('li',item));mapping.append(list);}
   say(pending?'A pending request is retained. Retry to recover the same revision or specialist.':'Custom templates ready. Save the current lightweight draft to reuse it.');
 });
 return {reset(){opened=null;title.value='Untitled custom specialist';if(data)fill(data.defaults);}};
}
