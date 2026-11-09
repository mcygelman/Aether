/* Own the native Tasker project view and explicit operator actions.
 * Never reconstruct filesystem roots, infer acceptance, or enforce execution policy.
 * Status words come from the server; this view only arranges them.
 */
const $=id=>document.getElementById(id);
let project=new URLSearchParams(location.search).get('project'), current=null, busy=false, reviewVersion='', timer=null, admin=null;
const requestKey='aether-native-project-submission';
let submission=sessionStorage.getItem(requestKey)||crypto.randomUUID();
sessionStorage.setItem(requestKey,submission);
const SPRINT_WORDS={running:'Running',closing:'Closing',
 'closed-pass':'Closed by the lead as done','closed-fail':'Closed by the lead as failed'};
const LIVE=['running','stopping'];
// Michel, 11-07: "Being set up" sat beside "Ready to start" and stayed while the lead worked (F4).
// A stopped sprint read "Running" the same way (found on the scratch check).
function sprintWords(v){if(String(v.state).startsWith('closed-'))return SPRINT_WORDS[v.state]||v.state;
 if(v.execution==='prepared')return 'Not started';if(LIVE.includes(v.execution))return v.state==='closing'?'Closing':'In progress';
 return 'Not closed by the lead';}
function node(tag,value,parent,cls){const e=document.createElement(tag);e.textContent=value??'';if(cls)e.className=cls;if(parent)parent.append(e);return e;}
function button(text,parent,onclick){const b=node('button',text,parent);b.type='button';b.onclick=onclick;return b;}
async function api(action,body,query=''){
 const r=await fetch('/api/tasker/'+action+query,{cache:'no-store',credentials:'same-origin',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
 const v=await r.json();if(!r.ok)throw Error(v.error||'Request failed');return v;
}
async function act(operation,done=''){if(busy)return;busy=true;$('notice').className='';$('notice').textContent='Working…';
 try{await operation();$('notice').textContent=done;}catch(e){$('notice').className='error';$('notice').textContent=e.message;}finally{busy=false;}}
function label(task,name){return `${task==='solo'?'Lead':'T'+task} · ${name}`;}
function outputUrl(task,name){return '/api/tasker/output?'+new URLSearchParams({project,task,name});}
function describe(check){
 const parts=[];if(check.json!==undefined)parts.push('exact JSON value');if(check.nonempty)parts.push('not empty');
 if(check.required?.length)parts.push('must contain: '+check.required.join(', '));if(check.forbidden?.length)parts.push('must not contain: '+check.forbidden.join(', '));
 for(const key of Object.keys(check))if(!['json','nonempty','required','forbidden'].includes(key))parts.push(key);
 return parts.join('; ')||'present';
}
async function preview(task,name,target,toggle){
 if(!target.hidden){target.hidden=true;toggle.textContent='Show';return;}
 target.replaceChildren();
 try{const raw=new Uint8Array(await (await fetch(outputUrl(task,name),{cache:'no-store',credentials:'same-origin'})).arrayBuffer());
  // An SVG is drawn as a picture. As an image it never runs the file's scripts or loads anything else; animation plays.
  if(/\.svg$/i.test(name)&&raw.length&&raw.length<=1048576){const img=node('img','',target,'output-picture');img.alt=label(task,name);
   img.onload=()=>URL.revokeObjectURL(img.src);img.onerror=()=>img.replaceWith(node('pre','This picture could not be drawn. Download it to read its code.'));
   img.src=URL.createObjectURL(new Blob([raw],{type:'image/svg+xml'}));}
  else node('pre',raw.length>262144||raw.includes(0)?'This file is large or not text. Download it to read it.':new TextDecoder().decode(raw)||'(empty file)',target);}
 catch(e){node('pre','Could not read the file: '+e.message,target);}
 target.hidden=false;toggle.textContent='Hide';
}
function renderBrief(v){
 $('brief-goals').textContent=v.goals;$('brief-model').textContent='Model connection: '+(v.deployment||'not set');
 $('brief-checks').replaceChildren();
 for(const [task,contract] of Object.entries(v.contracts||{}))for(const [name,check] of Object.entries(contract.files||{}))
  node('li',`${label(task,name)}: ${describe(check)}${contract.review_required?'; your review needed':''}`,$('brief-checks'));
 $('brief-record').textContent=JSON.stringify(v.contracts,null,2);
}
function renderProgress(v){
 $('headline').textContent=v.headline||v.execution;$('progress').replaceChildren();
 for(const [name,value] of [['Lead',v.lead_name||v.overseer],['Staff',(v.staff_names||v.staff||[]).join(', ')||'None hired'],['Sprint',sprintWords(v)],
  ['Budget',`${v.used.turns} of ${v.caps.max_turns_per_sprint} turns · $${(Number(v.used.cost)||0).toFixed(2)} of $${(Number(v.caps.max_cost_per_sprint)||0).toFixed(2)} (estimated)`]]){node('dt',name,$('progress'));node('dd',value,$('progress'));}
 // Michel, 11-07: a stop reads in plain words; the raw reason stays in the technical record (F5).
 $('detail').textContent=v.failure_words||v.terminal_failure||(v.execution!=='interrupted'?v.run.error||'':
  v.can_continue?'The app closed between turns, so nothing was cut off. Continue carries the work on from its records.':
  (v.open_turns||[]).length?`The app closed while ${v.open_turns.map(m=>m==='overseer'?'the lead':m).join(' and ')} was in the middle of a turn. So that nothing runs twice, the work cannot continue; it is kept as it is.`:
  v.run.error||'The process running this work is gone. The work will not restart on reload; keep this project for recovery.');
 $('lines').replaceChildren();
 for(const line of v.operator_lines||[]){const p=node('p','',$('lines'),line.stop?'danger':'');node('strong',line.stop?`The lead needs your answer (${line.stop}): `:'Message from the lead: ',p);p.append(line.text);}
 const live=LIVE.includes(v.execution);
 $('paused').hidden=!(live&&v.paused_for);$('paused-text').textContent=`The work is paused for your answer about ${v.paused_for}. Read the lead's message above, then continue, or stop the work.`;
 $('capped').hidden=!(live&&v.cap_reached);
 if(!$('capped').hidden){$('capped-text').textContent=`The ${v.cap_reached==='max_turns_per_sprint'?'turn':'cost'} limit is reached. No new turns start until you raise it, or stop the work.`;
  if(!$('raise-budget').value)$('raise-budget').value=v.caps.max_cost_per_sprint;if(!$('raise-turns').value)$('raise-turns').value=v.caps.max_turns_per_sprint;}
 // Michel, 11-07: "We need a way forward and not be dead in the water" (F7). Accept anyway is the Admin's.
 $('override').hidden=!(v.can_accept_anyway&&admin);$('again').hidden=!v.can_accept_anyway;
 $('start').hidden=!v.can_start;$('continue').hidden=!v.can_continue;$('stop').hidden=!live;$('stop').disabled=v.execution==='stopping';
 $('freshness').textContent='Updated '+new Date(v.read_at).toLocaleTimeString()+(live?'. Updates every few seconds.':'. Reloading never starts work.');
}
function renderTasks(v){
 $('tasks').replaceChildren();
 for(const t of v.tasks){const row=node('article','',$('tasks'));node('strong',`T${t.number} · ${t.title}`,row);
  node('p',`${t.status} · ${t.assigned_to||'Unassigned'}${t.depends_on?.length?' · after '+t.depends_on.map(n=>'T'+n).join(', '):''}`,row,'muted');if(t.note)node('p',t.note,row);}
 if(!v.tasks.length)node('p',v.execution==='prepared'?'No tasks yet. The lead plans them after you start the work.':'contracts' in v&&'solo' in (v.contracts||{})?'The lead is doing this work alone.':'No tasks recorded.',$('tasks'),'muted');
}
function renderOutputs(v){
 $('outputs').replaceChildren();
 // Michel, 11-05: an accepted sprint's output can go to Creations; it is reviewed there and saved only by choice.
 const accepted=v.verification.status==='accepted'||!!v.operator_accepted?.current;let ready=0;
 for(const [task,files] of Object.entries(v.output_files||{}))for(const file of files){
  const row=node('article','',$('outputs'));node('strong',label(task,file.name),row);
  if(!file.ready){node('p','Not written yet.',row,'muted');continue;}
  ready++;const actions=node('p','',row);const show=button('Show',actions,()=>preview(task,file.name,view,show));
  actions.append(' ');const a=node('a','Download',actions);a.href=outputUrl(task,file.name);a.download=file.name;
  if(accepted&&admin){actions.append(' ');node('a','Save to Creations…',actions).href='/workspace?'+new URLSearchParams({view:'creations',sprint:project,task,file:file.name});}
  const view=node('div','',row);view.hidden=true;
 }
 // Michel, 11-07: an extra file is named for you, never opened here (F5).
 if((v.extra_outputs||[]).length)node('p','Also written, not asked for: '+v.extra_outputs.join(', ')+'. Files a sprint did not ask for are listed by name only; they are not checked or opened here.',$('outputs'),'muted');
 if(!$('outputs').childElementCount)node('p','No outputs declared.',$('outputs'),'muted');
 else if(accepted&&ready&&admin===false)node('p','An Admin can save these outputs to Creations.',$('outputs'),'muted');
 else if(!accepted&&ready&&admin)node('p','Once this sprint is accepted, Save to Creations… appears beside each output.',$('outputs'),'muted');
}
function renderReview(v){
 const status=v.verification.status,ended=!LIVE.includes(v.execution)&&v.execution!=='prepared';
 $('acceptance').textContent=status==='accepted'?'Accepted. Every output passed its checks'+(Object.keys(v.review_tasks).length?' and your review.':'.')
  :status==='review_required'?'Waiting for your review.':ended?v.headline:'Nothing to review yet. Outputs come here when the work finishes.';
 const decisions=status==='accepted'?{}:v.review_tasks;const version=JSON.stringify(decisions);
 if(version!==reviewVersion){reviewVersion=version;$('decisions').replaceChildren();
  for(const [task,result] of Object.entries(decisions)){const row=node('article','',$('decisions'));
   node('strong',(v.outputs[task]||[]).map(name=>label(task,name)).join(', ')||label(task,'output'),row);
   if(result.status!=='review_required'){node('p',result.words||result.status,row,'muted');continue;}
   const pick=node('label','Your decision',row);const select=node('select','',pick);select.dataset.task=task;select.dataset.receipt=result.receipt_sha256;
   for(const [value,text] of [['','Choose…'],['PASS','Pass'],['FAIL','Fail']]){const o=node('option',text,select);o.value=value;}
   const notes=node('label','Notes (what you checked, anything wrong)',row);const area=node('textarea','',notes);area.rows=3;area.dataset.notes=task;
  }}
 const open=$('decisions').querySelectorAll('select').length>0;
 $('accept').hidden=!open;$('accept').disabled=!v.can_accept;$('review-help').hidden=!open;
}
function render(v){current=v;$('title').textContent=v.name;document.title=v.name+' · Aether';
 $('subtitle').textContent=v.roadmap?'Sprint in the roadmap “'+v.roadmap.name+'”':'Sprint';
 renderBrief(v);renderProgress(v);renderTasks(v);renderOutputs(v);renderReview(v);
 $('log').textContent=(v.log||[]).join('\n')||'Nothing logged yet.';
 $('record').textContent=JSON.stringify({sprint:v.id,timer:v.timer,run:v.run,verification:v.verification,operator_lines:v.operator_lines,log:v.log},null,2);
 schedule();
}
function schedule(){clearTimeout(timer);if(current&&LIVE.includes(current.execution))timer=setTimeout(()=>{if(busy)return schedule();refresh().catch(e=>{$('notice').className='error';$('notice').textContent='Could not refresh: '+e.message;schedule();});},2000);}
async function refresh(){render(await api('project',undefined,'?project='+encodeURIComponent(project)));}
// Michel, 11-07: the goal named HELLO.txt while this field kept answer.txt, and the lead wrote both (F3). The field
// follows the first file the goal names until you type in it, and says so when the two differ.
const FILE=/\b[\w-]+(?:\.[\w-]+)*\.(?:txt|md|markdown|json|csv|html?|svg|xml|ya?ml|py|js|ts|css|sql|log)\b/i;
let fileTouched=false;
function goalFile(){return ($('goals').value.match(FILE)||[''])[0];}
function fileHint(){if(!fileTouched)$('filename').value=goalFile();const named=goalFile(),file=$('filename').value.trim()||named||'answer.txt';
 const differs=!!named&&named.toLowerCase()!==file.toLowerCase();$('file-note').className=differs?'attention':'muted';
 $('file-note').textContent=differs?`Your goal names ${named}, but the lead will be asked for ${file}. Make them the same, or the lead may write both.`:'The lead writes this file. When the sprint ends, open it and mark it Pass or Fail.';}
$('filename').oninput=()=>{fileTouched=!!$('filename').value.trim();fileHint();};$('goals').addEventListener('input',fileHint);
$('prepare').onsubmit=e=>{e.preventDefault();act(async()=>{const advanced=$('contracts').value.trim(),file=$('filename').value.trim()||goalFile()||'answer.txt';let contracts;
 try{contracts=advanced?JSON.parse(advanced):{solo:{version:1,review_required:true,files:{[file]:{nonempty:true}},evidence:[]}};}catch(_){throw Error('The advanced checks are not valid JSON.');}
 // The simple form is a solo project: the brief says so, so the lead's context guides it (no rule is enforced here).
 const goals=advanced?$('goals').value:$('goals').value.trimEnd()+`\n\nWork alone, without hiring staff, and write the result to ${file}.`;
 const v=await api('create',{request_id:submission,roadmap:$('roadmap').value,...($('step').value?{roadmap_step:$('step').value}:{}),name:$('name').value,goals,overseer:$('lead').value,deployment:$('deployment').value,contracts,caps:{max_cost_per_sprint:Number($('budget').value),max_turns_per_sprint:Number($('turns').value)}});sessionStorage.removeItem(requestKey);location.href='/projects/tasker?project='+encodeURIComponent(v.project_id);});};
$('start').onclick=()=>act(async()=>{render(await api('start',{project}));},'Started.');
$('stop').onclick=()=>act(async()=>{render(await api('stop',{project}));},'Stop requested. Turns already running finish first.');
$('continue').onclick=()=>act(async()=>{render(await api('continue',{project}));},'Continued from its records.');
$('staff-add').onclick=()=>act(async()=>{const v=await api('staff',{});fillStaff(v);return v;},'Added to your staff.');
$('resume').onclick=()=>act(async()=>{render(await api('resume',{project}));},'Continued.');
$('raise').onclick=()=>act(async()=>{render(await api('budget',{project,caps:{max_cost_per_sprint:Number($('raise-budget').value),max_turns_per_sprint:Number($('raise-turns').value)}}));},'Budget raised.');
$('refresh').onclick=()=>act(refresh);
$('anyway').onclick=()=>act(async()=>{const reason=$('anyway-reason').value.trim();if(!reason)throw Error('Say in a few words why you accept it anyway.');
 if(!confirm('Accept this sprint over its failed check? It is recorded as accepted by you, with your reason.'))return;
 render(await api('accept-anyway',{project,reason}));},'Accepted over the failed check. Your reason is recorded.');
$('again').onclick=()=>{location.href='/projects/tasker?'+new URLSearchParams({again:project,...(current?.roadmap?{roadmap:current.roadmap.key}:{})});};
$('accept').onclick=()=>act(async()=>{const reviews={};for(const select of $('decisions').querySelectorAll('select')){if(!select.value)throw Error('Choose Pass or Fail for every output.');
  const notes=$('decisions').querySelector(`textarea[data-notes="${select.dataset.task}"]`).value.trim();
  reviews[select.dataset.task]={verdict:select.value,receipt_sha256:select.dataset.receipt,...(notes?{notes}:{})};}
 const v=await api('accept',{project,...(Object.keys(reviews).length?{reviews}:{})});reviewVersion='';await refresh();
 if(v.status!=='accepted'&&!Object.values(reviews).some(r=>r.verdict==='FAIL'))throw Error('Not accepted: '+(current.headline||v.status));},'Your review is recorded.');
function fillLeads(v){$('lead').replaceChildren();for(const m of v.minds){const o=node('option',m.name+(!m.available?' (busy with other work)':''),$('lead'));o.value=m.id;}
 $('lead-form').hidden=v.minds.length>0;$('lead-add').hidden=!v.minds.length;
 $('setup').textContent=v.halted?'New work is stopped (Settings → Emergency Halt). Enable new requests there before preparing a sprint.':!v.roadmaps?.length?'No open roadmap yet. Add one to a mission in Missions, then start its sprint from Roadmaps.':!v.minds.length?'No lead yet. Give your first lead a name above.':!v.deployments.length?'No model connection yet. Add one in Settings → Models, then refresh this page.':'Start work uses this connection and may incur model charges.';
 $('prepare').querySelector('button[type=submit]').disabled=!v.roadmaps?.length||!v.minds.length||!v.deployments.length||v.halted;}
$('lead-add').onclick=()=>{$('lead-form').hidden=!$('lead-form').hidden;if(!$('lead-form').hidden)$('lead-name').focus();};
$('lead-create').onclick=()=>act(async()=>{const name=$('lead-name').value.trim();if(!name)throw Error('Give the lead a name.');
 const v=await api('lead',{name});fillLeads(v);const made=v.minds.find(m=>m.name===name);if(made)$('lead').value=made.id;$('lead-name').value='';$('lead-form').hidden=true;$('lead-add').hidden=false;},'Lead created.');
async function start(){if(project){$('prepare').hidden=true;$('work').hidden=false;await refresh();return;}
 const v=await api('options');
 // A sprint belongs to a roadmap (Michel, 2026-11-02); Start sprint on Roadmaps preselects it.
 const wanted=new URLSearchParams(location.search).get('roadmap');
 for(const r of v.roadmaps||[]){const o=node('option',r.name+(r.status?' · '+r.status:''),$('roadmap'));o.value=r.key;}
 if(wanted&&(v.roadmaps||[]).some(r=>r.key===wanted))$('roadmap').value=wanted;
 roadmapChoices=v.roadmaps||[];fillSteps(new URLSearchParams(location.search).get('step'));$('roadmap').onchange=()=>fillSteps();
 fillLeads(v);for(const d of v.deployments){const o=node('option',d,$('deployment'));o.value=d;}
 $('budget').value=v.caps.max_cost_per_sprint;$('turns').value=v.caps.max_turns_per_sprint;
 fillStaff(v);
 // Michel, 11-07: Prepare it again opens this form with the stopped sprint's choices filled in (F7).
 const again=new URLSearchParams(location.search).get('again');
 if(again){const old=await api('project',undefined,'?project='+encodeURIComponent(again));
  $('name').value=(old.name+' (again)').slice(0,120);
  $('goals').value=String(old.goals||'').replace(/\n\nWork alone, without hiring staff, and write the result to [^\n]*\.\s*$/,'');
  for(const [field,value] of [['lead',old.overseer],['deployment',old.deployment]])if([...$(field).options].some(o=>o.value===value))$(field).value=value;
  const solo=old.contracts?.solo;if(solo){$('filename').value=Object.keys(solo.files)[0]||'';fileTouched=true;}else if(old.contracts){$('contracts').value=JSON.stringify(old.contracts,null,2);$('contracts').closest('details').open=true;}
  if(old.caps){$('budget').value=old.caps.max_cost_per_sprint;$('turns').value=old.caps.max_turns_per_sprint;}
  filled={name:$('name').value,goals:$('goals').value};$('notice').textContent='Filled in from “'+old.name+'”. Change what needs changing, then prepare.';}
 fileHint();
}
// Michel, 11-03: a sprint can be the work of one of its roadmap's planned sprints; choosing one fills the name and
// brief (kept if you already changed them). Active Sprints' Start links here with ?roadmap=&step=.
let roadmapChoices=[],filled={name:'',goals:''};
function fillSteps(wanted){const steps=(roadmapChoices.find(r=>r.key===$('roadmap').value)||{}).steps||[];$('step').replaceChildren();
 node('option','A new sprint (not in the plan yet)',$('step')).value='';
 for(const s of steps)node('option',s.name,$('step')).value=s.id;
 $('step').value=wanted&&steps.some(s=>s.id===wanted)?wanted:'';$('step').onchange=useStep;useStep();}
function useStep(){const r=roadmapChoices.find(r=>r.key===$('roadmap').value),s=(r?.steps||[]).find(s=>s.id===$('step').value);
 const name=s?s.name:'',goals=s?`This sprint is "${s.name}" in the roadmap "${r.name}".\n\n${s.description}`.trim():'';
 if($('name').value===filled.name)$('name').value=name;if($('goals').value===filled.goals)$('goals').value=goals;filled={name,goals};fileHint();}
// Praxis, 11-03: 37 shipped staff, grouped by the catalog's categories; a team plan in the brief names them by ID.
function fillStaff(v){
 const staff=v.staff||[],missing=v.missing_staff||[],box=$('staff-line');box.replaceChildren();
 // Michel, 11-03: an older roster can take the newer standard staff in one step; nobody at home is replaced.
 $('staff-new').hidden=!missing.length;
 const named=missing.slice(0,3).map(m=>m.name).join(', ')+(missing.length>3?` and ${missing.length-3} more`:'');
 $('staff-new-text').textContent=`${missing.length} newer standard staff ${missing.length===1?'is':'are'} not in your staff yet (${named}). Adding them changes no one already there.`;
 if(!staff.length){node('p','No staff in this installation.',box);return;}
 const list=node('details','',box,'staff-list');node('summary',`Staff the lead can hire (${staff.length})`,list);
 node('p','In a team plan, name each person by the ID shown here.',list);
 const groups=new Map();for(const s of staff){if(!groups.has(s.category))groups.set(s.category,[]);groups.get(s.category).push(s);}
 for(const [category,people] of groups){node('p',category,list,'eyebrow');const ul=node('ul','',list);
  for(const s of people)node('li',`${s.name} (${s.id}): ${s.role}${s.available?'':' · busy in another sprint'}`,ul);}
}
// Creations takes an accepted sprint's output from an Admin, as it takes Chat's artifacts.
(window.aetherAccess||fetch('/api/access/session',{cache:'no-store',credentials:'same-origin'}).then(r=>r.json()))
 .then(info=>{admin=info?.user?.role==='admin';},()=>{admin=false;}).then(()=>{if(current){renderProgress(current);renderOutputs(current);}});
start().catch(e=>{$('notice').className='error';$('notice').textContent=e.message;});
