/* Owner: Creations showcase, artifact authoring, import, reading and the Chat- and sprint-copy entries.
 * Must never: execute imported content, publish artifacts, write agent memory, or own Teach workflows.
 */
import {fullArtifact, kinds, libraryData, teachTemplate} from './artifact-data.js';
import {$, action, announce, api, button, can, card, count, date, download, editor, el, empty, heading, link, mutate, overview, params, projectLink, search, select, store, tabs, viewerClose} from './kit.js';
import {creationProjects, projectContext, projectField, projectOptions} from './project-data.js';
import './visuals.js';
let openedArtifactLink=false;
async function openArtifact(record=null,kind='note',{copy=false}={}){
 if((record?.kind||kind)==='teachframe')return (await import('./teach.js')).editTeach(record,{copy});
 const a=record?await fullArtifact(record):null,revision=store.revision;
 const fromChat=a?.origin==='chat'&&!copy;
 let getVisual=()=>a?.visual||null;
 const fields=fromChat?[{name:'content',label:'Content',type:'textarea',rows:18,class:'editor-content',required:true,max:250000,value:a.content}]:[
 {name:'title',label:'Title',required:true,max:160,value:copy?(a?.title||'Creation').slice(0,150)+' — copy':a?.title||''},
 {name:'kind',label:'Type',type:'select',options:can('admin')?Object.entries(kinds):[['teachframe','Teach Frame']],value:a?.kind||kind},
 {name:'stage',label:'Status',type:'select',options:[['draft','Draft'],['ready','Published']],value:copy?'ready':a?.stage||'draft'},
 await projectField(a,copy),
 {name:'tags',label:'Tags',max:1200,value:(a?.tags||[]).join(', '),hint:'Separate tags with commas.'},
 {name:'source',label:'Source or context',type:'textarea',rows:2,max:2000,value:copy&&a?.origin==='chat'?'From Chat: '+a.source+' · '+a.title:a?.source||''},
 {name:'content',label:'Artifact',type:'textarea',rows:18,class:'editor-content',required:true,max:250000,value:a?.content||(kind==='teachframe'?teachTemplate:'')}];
 editor(copy?'Make a creation copy':a?'Edit '+a.title:'New creation',fromChat?'Edits are saved to the artifact in its source chat. Make a workspace copy to change its title, type, tags or sharing status.':'Shape a useful result for sharing. Published creations are marked for everyone signed in to this installation; nothing is posted outside it, and agent memory is never written.',fields,async v=>{
  if(fromChat)await api('/api/model-chat/save-artifact',{id:a.chat,artifact:a.id,text:v.content});
  else await mutate('artifact_save',{...v,visual:getVisual(),id:copy?undefined:a?.id,tags:v.tags.split(',').map(t=>t.trim()).filter(Boolean)},revision);
 },{wide:true,saveLabel:copy?'Publish Creation':'Save creation',readonly:!can('admin')});
 getVisual=window.aetherVisuals.editor(a?.visual,!fromChat&&can('admin'),a?.title||'Creation',a?.kind||kind);
}

async function readCreation(record){
 if(record.kind==='teachframe')return (await import('./teach.js')).readTeach(record);
 const a=await fullArtifact(record),dialog=$('editor');dialog.className='wide';$('editor-title').textContent=a.title;$('editor-intro').textContent=(kinds[a.kind]||a.kind)+' · '+(a.archived?'Archived':a.stage==='ready'?'Published':'Draft')+' · '+(a.origin==='chat'?'From Chat':'Workspace');$('editor-error').textContent='';
 const details=el('dl',{class:'teach-details'});for(const [label,value]of [['Source / context',a.source],['Tags',(a.tags||[]).join(', ')],['Updated',date(a.updated_at||a.at)]])if(value)details.append(el('dt',{text:label}),el('dd',{text:value}));
 $('editor-fields').replaceChildren(el('p',{},await projectContext(a)),details,window.aetherVisuals.reader(a.content,a.visual,a.title,'creation-reading',a.kind));$('editor-cancel').textContent='Close';$('editor-save').hidden=true;const actions=$('editor-actions');actions.replaceChildren($('editor-cancel'),$('editor-save'));
 for(const format of ['text','json','xml'])actions.append(button('Export '+format.toUpperCase(),()=>download(a,a.title,format)));
 if(a.origin==='chat')actions.append(link('Source chat','/chat?chat='+encodeURIComponent(a.chat)+'&artifact='+encodeURIComponent(a.id)));
 if(can('admin')){actions.append(button('Make a copy',()=>{dialog.close();openArtifact(a,a.kind,{copy:true}).catch(e=>announce(e.message,true));}));actions.append(button('Edit',()=>{dialog.close();openArtifact(a).catch(e=>announce(e.message,true));}));}
 viewerClose();$('editor-form').onsubmit=e=>e.preventDefault();dialog.showModal();dialog.scrollTop=0;
}
async function importCreation(file){try{
 if(file.size>1024*1024)throw Error('Choose a creation no larger than 1 MiB.');let a;
 if(/\.(png|jpe?g|webp|gif)$/i.test(file.name)){const visual=await window.aetherVisuals.fromFile(file);a={kind:'note',title:file.name.replace(/\.[^.]+$/,'').slice(0,160),content:'Image: '+file.name,visual,tags:[],source:'Imported from '+file.name};}
 else{const content=await file.text();
 if(file.name.toLowerCase().endsWith('.json')){const v=JSON.parse(content);if(!v||Array.isArray(v)||!Object.hasOwn(kinds,v.kind)||typeof v.content!=='string')throw Error('Choose an exported creation JSON file with a type and content.');a={kind:v.kind,title:String(v.title||file.name).slice(0,160),content:v.content,visual:v.visual||null,tags:Array.isArray(v.tags)?v.tags.filter(t=>typeof t==='string').slice(0,20):[],source:String(v.source||'Imported from '+file.name).slice(0,2000),teaching:v.teaching&&typeof v.teaching==='object'?v.teaching:{}};}
 else{if(!/\.(txt|md|html?|svg)$/i.test(file.name)||content.includes('\u0000')||content.includes('\ufffd'))throw Error('Choose UTF-8 text, Markdown, HTML, SVG or exported creation JSON.');a={kind:/\.html?$/i.test(file.name)?'html':/\.svg$/i.test(file.name)?'svg':'note',title:file.name.replace(/\.[^.]+$/,'').slice(0,160),content,tags:[],source:'Imported from '+file.name};}
 }
 if(!a.content.trim()||a.content.length>250000)throw Error('Use a nonempty creation up to 250,000 characters.');await openArtifact({...a,origin:'import'},a.kind,{copy:true});$('edit-title').value=a.title;$('editor-title').textContent='Import creation';$('editor-intro').textContent='Check the title, type and text, then Publish Creation. The original file stays unchanged.';
 }catch(e){announce(e.message,true);}}
// An accepted sprint's output, opened as a workspace copy to review before saving (the sprint page links here).
async function sprintCopy(sprint,task,name){
 if(!can('admin'))throw Error('An Admin can add Creations.');
 const v=await api('/api/tasker/project?'+new URLSearchParams({project:sprint}));
 if(v.verification?.status!=='accepted'&&!v.operator_accepted?.current)throw Error('Only an accepted sprint’s outputs can be saved to Creations. Review the sprint first.');
 const r=await fetch('/api/tasker/output?'+new URLSearchParams({project:sprint,task,name}),{cache:'no-store',credentials:'same-origin'});
 if(!r.ok)throw Error((await r.json().catch(()=>({}))).error||'This sprint output is unavailable.');
 let content;try{content=new TextDecoder('utf-8',{fatal:true}).decode(await r.arrayBuffer());}catch(_){throw Error('Only text outputs (SVG, HTML, Markdown or plain text) can be saved to Creations. Download this one instead.');}
 if(!content.trim()||content.length>250000||content.includes('\u0000'))throw Error('Use a nonempty text output up to 250,000 characters.');
 const a={kind:/\.html?$/i.test(name)?'html':/\.svg$/i.test(name)?'svg':'note',title:(v.name+' · '+name.replace(/\.[^.]+$/,'')).slice(0,160),content,tags:[],project_id:v.roadmap?.key||'',source:('From the sprint “'+v.name+'”: '+(task==='solo'?'Lead':'T'+task)+' · '+name).slice(0,2000)};
 await openArtifact({...a,origin:'import'},a.kind,{copy:true});$('edit-title').value=a.title;$('editor-title').textContent='Save to Creations';$('editor-intro').textContent='Check the title, type and text, then Publish Creation. This adds a copy to Creations; the sprint’s file stays as it is.';
}
async function libraryPage(){
 const projectRows=await creationProjects(),projectNames=new Map(projectRows.map(p=>[p.id,p.name]));
 const importer=el('input',{type:'file',accept:'.txt,.md,.json,.html,.htm,.png,.jpg,.jpeg,.webp,.gif,.svg',hidden:true,'aria-label':'Import creation file'});importer.onchange=()=>{const file=importer.files[0];if(file)importCreation(file);importer.value='';};
 heading('Creations','Showcase useful work from your ecosystem, or import an artifact to view and share.',[button('Import creation',()=>importer.click(),'','admin'),button('＋ New creation',()=>openArtifact().catch(e=>announce(e.message,true)),'primary','admin')]);
 let rows=store.artifacts.map(a=>({...a,origin:'workspace'}));try{rows.push(...(await libraryData()).filter(a=>a.origin==='chat'));}catch(e){announce('Workspace creations are shown. Chat creations could not be loaded; refresh to try again.',true);}
 let q='',mode='current',kind='all',origin='all',sort='recent',project=params.get('project')||'all';const root=$('page-content'),note=el('p',{class:'result-note','aria-live':'polite'}),list=el('div',{class:'grid'});
 const projectChoices=[['all','All projects'],['','No project'],...projectOptions(projectRows)];
 for(const id of new Set([...rows.map(a=>a.project_id),params.get('project')].filter(Boolean)))if(!projectNames.has(id))projectChoices.push([id,'Unavailable project · '+id]);
 root.append(importer,overview([[rows.filter(a=>!a.archived).length,'Current creations'],[rows.filter(a=>!a.archived&&a.stage==='ready').length,'Published'],[rows.filter(a=>a.origin==='chat').length,'From Chat']]));
 root.append(el('div',{class:'toolbar'},search('Search titles, sources and tags…',v=>{q=v;render();}),el('label',{class:'filter-control',text:'Status'},select('Status',[['current','Current'],['draft','Drafts'],['ready','Published'],['archived','Archived']],mode,v=>{mode=v;render();})),select('Creation type',[['all','All types'],...Object.entries(kinds)],kind,v=>{kind=v;render();}),select('Creation source',[['all','All sources'],['workspace','Workspace'],['chat','From Chat']],origin,v=>{origin=v;render();}),el('label',{class:'filter-control',text:'Sort By'},select('Sort By',[['recent','Recently updated'],['title','Title A–Z'],['title-desc','Title Z–A']],sort,v=>{sort=v;render();}))),note,list);
 root.querySelector('.toolbar').append(select('Creation project',projectChoices,project,v=>{project=v;render();}));
 root.append(el('p',{class:'small',text:'Workspace creations are visible to signed-in operators here. Optional project links organize that shared collection. Chat artifacts remain private until you deliberately save a workspace copy. Saving does not send work to agents or change their memory.'}));
 function render(){const shown=rows.filter(a=>(project==='all'||(a.project_id||'')===project)&&(mode==='archived'?a.archived:!a.archived&&(mode==='current'||(mode==='ready'?a.stage==='ready':a.stage!=='ready')))&&(kind==='all'||a.kind===kind)&&(origin==='all'||a.origin===origin)&&(!q||(a.title+' '+(a.source||'')+' '+(projectNames.get(a.project_id)||'')+' '+(a.tags||[]).join(' ')).toLocaleLowerCase().includes(q))).sort((a,b)=>sort==='title-desc'?b.title.localeCompare(a.title):sort==='title'?a.title.localeCompare(b.title):String(b.updated_at||b.at||'').localeCompare(String(a.updated_at||a.at||'')));
 note.textContent=count(shown.length,'creation');list.replaceChildren();for(const a of shown){const c=card(a.title);c.prepend(el('div',{class:'eyebrow',text:(kinds[a.kind]||a.kind)+' · '+(a.archived?'Archived':a.stage==='ready'?'Published':'Draft')}));
 if(a.origin==='workspace')c.append(el('p',{class:'creation-excerpt',text:a.kind==='html'?'HTML page · Open to preview':a.visual?'Image attached · '+a.visual.name:window.aetherVisuals.svgSource(a.content)?'SVG illustration · Open to preview':(a.content||'').slice(0,220)+((a.content||'').length>220?'…':'')}));if(a.source)c.append(el('p',{class:'muted',text:a.source}));c.append(el('div',{class:'small',text:(a.origin==='chat'?'From Chat':'Workspace')+' · updated '+date(a.updated_at||a.at)}));if(a.tags?.length)c.append(el('div',{class:'tags'},...a.tags.map(t=>el('span',{class:'pill',text:t}))));
 const actions=el('div',{class:'actions'},button('View creation',()=>readCreation(a).catch(e=>announce(e.message,true)),'primary'));if(can('admin')||(a.kind==='teachframe'&&can('manage_teach'))){actions.append(button('Edit',()=>openArtifact(a).catch(e=>announce(e.message,true))));if(a.origin==='workspace')actions.append(button(a.archived?'Restore':'Archive',()=>action(()=>mutate('artifact_archive',{id:a.id,archived:!a.archived}),a.archived?'Creation restored.':'Creation archived.')));}
 if(a.origin==='workspace'&&can('admin'))actions.append(button('Delete',()=>{if(confirm('Delete “'+a.title+'” from Creations? This cannot be undone. Archive hides it and keeps it instead.'))action(()=>mutate('artifact_delete',{id:a.id}),'Creation deleted.');},'danger'));
 if(a.project_id)c.append(el('p',{},projectNames.has(a.project_id)?link('Project: '+projectNames.get(a.project_id),projectLink({id:a.project_id}),''):el('span',{class:'muted',text:'Linked project unavailable here'})));
 if(a.origin==='chat')actions.append(link('Source chat','/chat?chat='+encodeURIComponent(a.chat)+'&artifact='+encodeURIComponent(a.id)));c.append(actions);list.append(c);}
 if(!shown.length)list.append(empty('No creations in this view',rows.length?'Try another type, source, status or search.':'Create or import a result here, or use Transform in Chat.',link('Open Chat','/')));}
 render();
 if(!openedArtifactLink){openedArtifactLink=true;const id=params.get('artifact'),chat=params.get('chat');if(id&&chat&&params.get('copy')==='1'){const a=rows.find(a=>a.origin==='chat'&&a.chat===chat&&a.id===id);if(!a)announce('This Chat artifact is unavailable to your signed-in account.',true);else if(can('admin')||a.kind==='teachframe'&&can('manage_teach'))await openArtifact(a,a.kind,{copy:true});else announce('An Admin can add Creations; Manage Teach also allows Teach Frames.',true);}else if(id){const a=rows.find(a=>a.origin==='workspace'&&a.id===id);if(a)await readCreation(a);else announce('This creation is unavailable. It may have been removed from this installation.',true);}else if(params.get('sprint'))await sprintCopy(params.get('sprint'),params.get('task'),params.get('file')).catch(e=>announce(e.message,true));else if(params.get('new')==='1'&&can('admin'))await openArtifact();}
}

export {libraryPage, readCreation};
