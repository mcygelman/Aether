/* Owner: Teach Frame authoring, import, reading and archive controls.
 * Must never: train an agent, write memory, publish a frame, or own ordinary Creations.
 */
import {fullArtifact, libraryData} from './artifact-data.js';
import {$, action, announce, api, button, can, card, count, date, download, editor, el, empty, heading, link, mutate, overview, search, select, store, tabs, viewerClose} from './kit.js';
import {projectContext, projectField} from './project-data.js';
import './visuals.js';
const teachGuide='[ TITLE ]\nName the approach.\n\n[ WHEN TO USE ]\nDescribe the situation this guidance fits, and when it does not.\n\n[ APPROACH ]\nExplain the steps and the reasons behind them.\n\n[ EXAMPLE ]\nShow a concrete application.\n\n[ CHECKS ]\nDescribe what success looks like and what to inspect.\n\n[ LIMITATIONS & ADAPTATION ]\nRecord uncertainty, failure cases, and when to stop or adapt.\n\n[ EVIDENCE & ATTRIBUTION ]\nCredit contributors and link the experience or sources behind this guidance.\n';
function teachDetails(a){return a.teaching||{};}
async function editTeach(record=null,{copy=false}={}){
 const a=record?await fullArtifact(record):null,t=teachDetails(a||{}),revision=store.revision,fromChat=a?.origin==='chat'&&!copy;
 let getVisual=()=>a?.visual||null;
 const fields=[{name:'title',label:'Title',required:true,max:160,value:copy?((a?.title||'Teach Frame').slice(0,150)+' — copy'):a?.title||''},
 {name:'content',label:'Guidance',type:'textarea',rows:16,class:'editor-content',required:true,max:250000,value:a?.content||teachGuide}];
 if(!fromChat)fields.push({name:'stage',label:'Status',type:'select',options:[['draft','Draft'],['ready','Ready to share']],value:copy?'draft':a?.stage||'draft'},
 {name:'school',label:'School or topic',max:120,value:t.school||''},{name:'audience',label:'Who is this for?',max:500,value:t.audience||''},
 {name:'when_to_use',label:'When to use this approach',type:'textarea',rows:3,max:3000,value:t.when_to_use||''},
 {name:'prerequisites',label:'Prerequisites',type:'textarea',rows:2,max:1500,value:t.prerequisites||''},
 {name:'tags',label:'Tags',max:1200,value:(a?.tags||[]).join(', '),hint:'Separate tags with commas.'},
 {name:'source',label:'Evidence, attribution or source',type:'textarea',rows:2,max:2000,value:copy&&a?.origin==='chat'?'From Chat: '+a.title:a?.source||''});
 if(!fromChat)fields.splice(2,0,await projectField(a,copy));
 editor(copy?'Make a Teach Frame copy':a?'Edit Teach Frame':'New Teach Frame',fromChat?'Edits update this artifact in your source chat. Make a workspace copy to add teaching details and a sharing status.':'Write guidance another agent can use. Ready to share is your label; saving does not train an agent or change its memory.',fields,async v=>{
 if(fromChat)await api('/api/model-chat/save-artifact',{id:a.chat,artifact:a.id,text:v.content});
 else await mutate('artifact_save',{id:copy?undefined:a?.id,kind:'teachframe',title:v.title,content:v.content,visual:getVisual(),stage:v.stage,project_id:v.project_id,tags:v.tags.split(',').map(x=>x.trim()).filter(Boolean),source:v.source,teaching:{school:v.school,audience:v.audience,when_to_use:v.when_to_use,prerequisites:v.prerequisites}},revision);
 },{wide:true,saveLabel:copy?'Save copy':'Save Teach Frame',readonly:!can('manage_teach')});
 getVisual=window.aetherVisuals.editor(a?.visual,!fromChat&&can('manage_teach'),a?.title||'Teach Frame');
 // Chat titles belong to the conversation; its artifact API only edits content.
 if(fromChat)$('edit-title').readOnly=true;
}
async function readTeach(record){const a=await fullArtifact(record),t=teachDetails(a);const dialog=$('editor');dialog.className='wide';$('editor-title').textContent=a.title;$('editor-intro').textContent=(a.archived?'Archived':a.stage==='ready'?'Ready to share':'Draft')+' · '+(a.origin==='chat'?'From Chat':'Workspace');$('editor-error').textContent='';$('editor-fields').replaceChildren();
 const details=el('dl',{class:'teach-details'});for(const [label,value]of [['School / topic',t.school],['Audience',t.audience],['When to use',t.when_to_use],['Prerequisites',t.prerequisites],['Evidence / source',a.source]])if(value)details.append(el('dt',{text:label}),el('dd',{text:value}));
 $('editor-fields').append(el('p',{},await projectContext(a)),details,window.aetherVisuals.reader(a.content,a.visual,a.title,'teach-reading'));const actions=$('editor-actions');$('editor-cancel').textContent='Close';$('editor-save').hidden=true;actions.replaceChildren($('editor-cancel'),$('editor-save'));
 if(a.origin==='chat')actions.append(link('Source chat','/chat?chat='+encodeURIComponent(a.chat)+'&artifact='+encodeURIComponent(a.id)));
 for(const format of ['text','json','xml'])actions.append(button('Export '+format.toUpperCase(),()=>download({...a,kind:'teachframe'},a.title,format)));
 if(can('manage_teach')){actions.append(button('Make a copy',()=>{dialog.close();editTeach(a,{copy:true}).catch(e=>announce(e.message,true));}));actions.append(button('Edit',()=>{dialog.close();editTeach(a).catch(e=>announce(e.message,true));}));}
 viewerClose();$('editor-form').onsubmit=e=>e.preventDefault();dialog.showModal();dialog.scrollTop=0;
}
async function importTeach(file){try{if(file.size>1024*1024)throw Error('Choose a Teach Frame no larger than 1 MiB.');const content=await file.text();let a;
 if(file.name.toLowerCase().endsWith('.json')){const v=JSON.parse(content);if(!v||Array.isArray(v)||v.kind!=='teachframe'||typeof v.content!=='string'||!v.content.trim())throw Error('Choose an exported Teach Frame JSON file.');a={title:String(v.title||file.name).slice(0,160),content:v.content,visual:v.visual||null,teaching:v.teaching&&typeof v.teaching==='object'?v.teaching:{},tags:Array.isArray(v.tags)?v.tags.filter(t=>typeof t==='string').slice(0,20):[],source:String(v.source||'Imported from '+file.name).slice(0,2000)};}
 else{if(!/\.(txt|md)$/i.test(file.name))throw Error('Choose a UTF-8 .txt, .md or exported Teach Frame .json file.');if(content.includes('\u0000')||content.includes('\ufffd'))throw Error('Save this frame as UTF-8 text before importing it.');const header=key=>content.match(new RegExp('⟪'+key+'::([^⟫]+)⟫','i'))?.[1]||content.match(new RegExp('^'+key+':\\s*(.+)$','im'))?.[1]||'';a={title:(content.match(/\[\s*TITLE\s*\]\s*\n([^\n]+)/i)?.[1]?.trim()||file.name.replace(/\.[^.]+$/,'')).slice(0,160),content,source:'Imported from '+file.name,tags:[],teaching:{school:header('SCHOOL').slice(0,120),audience:header('AUDIENCE').slice(0,500),prerequisites:header('PREREQUISITES').slice(0,1500)}};}
 if(!a.content.trim()||a.content.length>250000)throw Error('Use a nonempty Teach Frame up to 250,000 characters.');await editTeach({...a,kind:'teachframe',origin:'import'},{copy:true});$('edit-title').value=a.title;$('editor-title').textContent='Import Teach Frame';$('editor-intro').textContent='Review this draft and save a workspace copy. The source file stays unchanged.';
 }catch(e){announce(e.message,true);}}
const schoolStates={CURRICULUM:'Curriculum',ARCHIVE:'Archived',CONSTITUTION:'Constitution'};
function schoolLabel(f){return (schoolStates[f.state]||f.state)+(f.version?' · v'+f.version:'');}
// The School's frames are what the AI staff are taught (teachframe_list); they are read here, never edited here.
async function readSchool(f){const v=await api('/api/tasker/school-frame?name='+encodeURIComponent(f.name));const dialog=$('editor');dialog.className='wide';$('editor-title').textContent=f.title;$('editor-intro').textContent=f.school+' · '+schoolLabel(f)+' · what the AI staff are taught; read-only here';$('editor-error').textContent='';$('editor-fields').replaceChildren();
 const details=el('dl',{class:'teach-details'});for(const [label,value]of [['Audience',f.audience],['Prevents',f.prevents],['Prerequisites',f.prerequisites.join(', ')],['Recommended for',f.recommended_for.join(', ')],['Evidence',f.qualification]])if(value)details.append(el('dt',{text:label}),el('dd',{text:value}));
 $('editor-fields').append(details,window.aetherVisuals.reader(v.text,null,f.title,'teach-reading'));const actions=$('editor-actions');$('editor-cancel').textContent='Close';$('editor-save').hidden=true;actions.replaceChildren($('editor-cancel'),$('editor-save'));
 actions.append(button('Export TEXT',()=>download(v.text,f.name,'text')));
 if(can('manage_teach'))actions.append(button('Make a workspace copy',()=>{dialog.close();schoolCopy(f,v.text).catch(e=>announce(e.message,true));}));
 viewerClose();$('editor-form').onsubmit=e=>e.preventDefault();dialog.showModal();dialog.scrollTop=0;
}
async function schoolCopy(f,text){await editTeach({title:f.title,content:text,kind:'teachframe',origin:'import',tags:['school'],source:'From the School: '+f.name+(f.version?' v'+f.version:''),teaching:{school:f.school,audience:f.audience,prerequisites:f.prerequisites.join(', ')}},{copy:true});$('edit-title').value=f.title.slice(0,160);$('editor-title').textContent='Copy a School frame';$('editor-intro').textContent='Adapt this workspace copy freely. The School frame the AI staff are taught stays unchanged.';}
async function teachPage(){
 const importer=el('input',{type:'file',accept:'.txt,.md,.json',hidden:true,'aria-label':'Import Teach Frame file'});importer.onchange=()=>{const f=importer.files[0];if(f)importTeach(f);importer.value='';};
 heading('Teach Frames','Capture an approach, explain when it fits, and prepare it for another agent to use.',[button('Import frame',()=>importer.click(),'', 'manage_teach'),button('＋ New Teach Frame',()=>editTeach().catch(e=>announce(e.message,true)),'primary','manage_teach')]);
 let rows=store.artifacts.filter(a=>a.kind==='teachframe').map(a=>({...a,origin:'workspace'}));try{rows.push(...(await libraryData()).filter(a=>a.kind==='teachframe'&&a.origin==='chat'));}catch(e){announce('Workspace frames are shown. Chat frames could not be loaded; refresh to try again.',true);}
 let school=[],schoolError='';try{school=(await api('/api/tasker/school')).frames;}catch(e){schoolError=e.message;}
 let q='',mode='current',origin='all',sort='recent';const root=$('page-content'),list=el('div',{class:'grid'}),note=el('p',{class:'result-note','aria-live':'polite'});
 const schoolHead=el('h2',{text:'From the School'}),schoolIntro=el('p',{class:'muted',text:'What the AI staff are taught. Read a frame here; make a workspace copy to adapt it.'}),schoolList=el('div',{class:'grid'}),ownHead=el('h2',{text:'Workspace and Chat'});
 root.append(importer,overview([[school.filter(f=>f.state==='CURRICULUM').length,'In the School'],[rows.filter(a=>!a.archived).length,'Current frames'],[rows.filter(a=>!a.archived&&a.stage==='ready').length,'Ready to share'],[rows.filter(a=>a.origin==='chat').length,'From Chat']]));
 root.append(el('div',{class:'toolbar'},search('Search titles, topics and tags…',v=>{q=v;render();}),tabs('Frame status',[['current','Current'],['draft','Drafts'],['ready','Ready to share'],['archived','Archived']],mode,v=>{mode=v;render();}),select('Frame source',[['all','All sources'],['school','From the School'],['workspace','Workspace'],['chat','From Chat']],origin,v=>{origin=v;render();}),select('Sort frames',[['recent','Recently updated'],['title','Title A–Z']],sort,v=>{sort=v;render();})),note,schoolHead,schoolIntro,schoolList,ownHead,list);
 root.append(el('p',{class:'small',text:'Read and export frames freely. Manage Teach allows authoring and archiving. Share a polished frame through its project; saving here never writes agent memory.'}));
 function render(){const shown=rows.filter(a=>(mode==='archived'?a.archived:!a.archived&&(mode==='current'||(mode==='ready'?a.stage==='ready':a.stage!=='ready')))&&(origin==='all'||a.origin===origin)&&(!q||(a.title+' '+a.content+' '+(a.source||'')+' '+JSON.stringify(teachDetails(a))+' '+(a.tags||[]).join(' ')).toLocaleLowerCase().includes(q))).sort((a,b)=>sort==='title'?a.title.localeCompare(b.title):String(b.updated_at||b.at||'').localeCompare(String(a.updated_at||a.at||'')));
 const schoolOn=(origin==='all'||origin==='school')&&(mode==='current'||mode==='archived');
 const schoolShown=schoolOn?school.filter(f=>(mode==='archived'?f.state==='ARCHIVE':f.state!=='ARCHIVE')&&(!q||[f.name,f.title,f.school,f.audience,f.prevents,f.recommended_for.join(' ')].join(' ').toLocaleLowerCase().includes(q))).sort((a,b)=>sort==='title'?a.title.localeCompare(b.title):String(b.date).localeCompare(String(a.date))):[];
 const ownOn=origin!=='school';schoolHead.hidden=schoolIntro.hidden=schoolList.hidden=!schoolOn;ownHead.hidden=!(ownOn&&schoolOn);list.hidden=!ownOn;
 schoolList.replaceChildren();if(schoolOn&&schoolError)schoolList.append(el('p',{class:'muted',text:'School frames could not be loaded: '+schoolError}));
 for(const f of schoolShown){const c=card(f.title);c.prepend(el('div',{class:'eyebrow',text:f.school+' · '+schoolLabel(f)}));if(f.prevents)c.append(el('p',{text:'Prevents '+f.prevents.slice(0,220)+(f.prevents.length>220?'…':'')}));if(f.audience)c.append(el('p',{class:'muted',text:'For '+f.audience.slice(0,220)+(f.audience.length>220?'…':'')}));if(f.recommended_for.length)c.append(el('div',{class:'small',text:'Recommended for '+f.recommended_for.join(', ')}));c.append(el('div',{class:'actions'},button('Read frame',()=>readSchool(f).catch(e=>announce(e.message,true)),'primary')));schoolList.append(c);}
 if(schoolOn&&!schoolError&&!schoolShown.length)schoolList.append(el('p',{class:'muted',text:'No School frames in this view.'}));
 note.textContent=count((ownOn?shown.length:0)+schoolShown.length,'Teach Frame');list.replaceChildren();for(const a of shown){const t=teachDetails(a),c=card(a.title);c.prepend(el('div',{class:'eyebrow',text:(t.school||'Teach Frame')+' · '+(a.archived?'Archived':a.stage==='ready'?'Ready to share':'Draft')}));if(t.when_to_use)c.append(el('p',{text:t.when_to_use.slice(0,220)+(t.when_to_use.length>220?'…':'')}));if(t.audience)c.append(el('p',{class:'muted',text:'For '+t.audience}));c.append(el('div',{class:'small',text:(a.origin==='chat'?'From Chat':'Workspace')+' · updated '+date(a.updated_at||a.at)}));if(a.tags?.length)c.append(el('div',{class:'tags'},...a.tags.map(t=>el('span',{class:'pill',text:t}))));const actions=el('div',{class:'actions'},button('Read frame',()=>readTeach(a).catch(e=>announce(e.message,true)),'primary'));
 if(can('manage_teach')){actions.append(button('Edit',()=>editTeach(a).catch(e=>announce(e.message,true))));if(a.origin==='workspace')actions.append(button(a.archived?'Restore':'Archive',()=>action(()=>mutate('artifact_archive',{id:a.id,archived:!a.archived}),a.archived?'Teach Frame restored.':'Teach Frame archived.')));}
 if(a.origin==='chat')actions.append(link('Source chat','/chat?chat='+encodeURIComponent(a.chat)+'&artifact='+encodeURIComponent(a.id)));c.append(actions);list.append(c);}
 if(!shown.length)list.append(schoolShown.length?el('p',{class:'muted',text:'No workspace or Chat frames in this view yet.'}):empty('No Teach Frames in this view','Create a frame, import an existing text file, or use Transform → Teach Frame in Chat.',link('Open Chat','/')));}
 render();
}

export {editTeach, readTeach, teachPage};
