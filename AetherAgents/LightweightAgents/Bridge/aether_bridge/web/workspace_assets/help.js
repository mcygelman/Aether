/* Owner: Help navigation, authored Guides, bug notes, logs and lexicon.
 * Must never: write agent memory, execute guide markup, or own unrelated area workflows.
 */
import {$, action, announce, api, button, can, card, count, date, download, editor, el, empty, heading, link, mutate, overview, search, select, store, tabs, topic, viewerClose, workspaceLink} from './kit.js';
import {guides, helpTopics} from './help-data.js';
import {projects} from './project-data.js';
function editGuide(a=null,{copy=false}={}){
 const revision=store.revision;
 editor(copy?'Make a guide copy':a?'Edit guide':'New guide','Build the operator Guides collection. Draft and Ready to use are labels; every saved guide is readable by signed-in operators.',[
  {name:'title',label:'Title',required:true,max:160,value:copy?(a.title.slice(0,150)+' — copy'):a?.title||''},
  {name:'summary',label:'Summary',type:'textarea',rows:2,max:1000,value:a?.summary||''},
  {name:'category',label:'Category',max:120,value:a?.category||''},
  {name:'stage',label:'Status',type:'select',options:[['draft','Draft'],['ready','Ready to use']],value:copy?'draft':a?.stage||'draft'},
  {name:'tags',label:'Tags',max:1200,value:(a?.tags||[]).join(', '),hint:'Separate tags with commas.'},
  {name:'source',label:'Author, attribution or source',type:'textarea',rows:2,max:2000,value:a?.source||''},
  {name:'content',label:'Guide text',type:'textarea',rows:18,class:'editor-content',required:true,max:250000,value:a?.content||'',hint:'Plain text or Markdown source. Markup is displayed as text.'}
 ],v=>mutate('guide_save',{...v,id:copy?undefined:a?.id,tags:v.tags.split(',').map(t=>t.trim()).filter(Boolean)},revision),{wide:true,saveLabel:copy?'Save copy':'Save guide',readonly:!can('manage_guides')});
}
function readGuide(a){
 const dialog=$('editor');dialog.className='wide';$('editor-title').textContent=a.title;$('editor-intro').textContent=(a.archived?'Archived · ':'')+(a.stage==='ready'?'Ready to use':'Draft')+' · '+(a.category||'General')+' · Updated '+date(a.updated_at);$('editor-error').textContent='';
 $('editor-fields').replaceChildren(el('p',{text:a.summary||''}),el('p',{class:'small',text:(a.tags||[]).join(' · ')}),el('pre',{class:'guide-reading',text:a.content}));
 if(a.source)$('editor-fields').append(el('p',{class:'small',text:'Author / source: '+a.source}));
 $('editor-cancel').textContent='Close';$('editor-save').hidden=true;$('editor-actions').replaceChildren($('editor-cancel'),$('editor-save'));
 for(const format of ['text','json'])$('editor-actions').append(button('Export '+format.toUpperCase(),()=>download(a,a.title,format)));
 if(can('manage_guides'))$('editor-actions').append(button('Make a copy',()=>{dialog.close();editGuide(a,{copy:true});}),button('Edit',()=>{dialog.close();editGuide(a);}));
 viewerClose();$('editor-form').onsubmit=e=>e.preventDefault();dialog.showModal();
}
async function importGuide(file){try{
 if(file.size>1024*1024)throw Error('Choose a guide no larger than 1 MiB.');
 const content=await file.text();let a;
 if(/\.json$/i.test(file.name)){
  const v=JSON.parse(content);if(!v||Array.isArray(v)||v.kind!=='guide'||typeof v.content!=='string'||!v.content.trim())throw Error('Choose an exported guide JSON file.');
  a={title:String(v.title||file.name).slice(0,160),content:v.content,summary:String(v.summary||'').slice(0,1000),category:String(v.category||'').slice(0,120),source:String(v.source||'Imported from '+file.name).slice(0,2000),tags:Array.isArray(v.tags)?v.tags.filter(t=>typeof t==='string').slice(0,20):[]};
 }else{
  if(!/\.(txt|md)$/i.test(file.name))throw Error('Choose a UTF-8 .txt, .md or exported guide .json file.');
  if(content.includes('\u0000')||content.includes('\ufffd'))throw Error('Save this guide as UTF-8 text before importing it.');
  a={title:(content.match(/^#\s+(.+)$/m)?.[1]||file.name.replace(/\.[^.]+$/,'')).slice(0,160),content,source:'Imported from '+file.name};
 }
 if(!a.content.trim()||a.content.length>250000)throw Error('A guide needs 1–250,000 characters of text.');
 editGuide(a);$('editor-title').textContent='Import guide';$('editor-intro').textContent='Review the imported text and details, then Save guide to add it to the collection.';
 }catch(e){announce(e.message,true);}
}
function guidesPage(){
 const body=helpLayout('Guides','Practical reference written and collected by your operators.');
 let q='',mode='current',category='all',sort='updated';const rows=store.guides||[],list=el('div',{class:'grid'}),results=el('p',{class:'result-note','aria-live':'polite'});
 const picker=el('input',{type:'file',accept:'.txt,.md,.json',hidden:true,'aria-label':'Import guide file'});picker.onchange=()=>{const file=picker.files[0];picker.value='';if(file)importGuide(file);};
 body.append(overview([[rows.filter(a=>!a.archived).length,'Current guides'],[rows.filter(a=>!a.archived&&a.stage==='ready').length,'Ready to use'],[new Set(rows.filter(a=>!a.archived).map(a=>a.category||'General')).size,'Categories']]),
  el('div',{class:'actions'},button('＋ New guide',()=>editGuide(),'primary','manage_guides'),button('Import guide',()=>picker.click(),'','manage_guides'),picker),
  el('div',{class:'toolbar'},search('Search guides, text and tags…',v=>{q=v;render();}),tabs('Guide status',[['current','Current'],['ready','Ready'],['draft','Drafts'],['archived','Archived'],['all','All']],mode,v=>{mode=v;render();}),select('Guide category',[['all','All categories'],...Array.from(new Set(rows.map(a=>a.category||'General'))).sort().map(c=>[c,c])],category,v=>{category=v;render();}),select('Sort guides',[['updated','Recently updated'],['title','Title']],sort,v=>{sort=v;render();})),results,list,
  el('p',{class:'small',text:can('manage_guides')?'Import UTF-8 text, Markdown or a guide JSON export. Review before saving; archive keeps the text available for restoring.':'You can read and export guides. An Admin can add Manage Guides to your operator account to enable authoring.'}));
 function render(){const shown=rows.filter(a=>(mode==='all'||(mode==='archived'?a.archived:!a.archived&&(mode==='current'||a.stage===mode)))&&(category==='all'||(a.category||'General')===category)&&(!q||[a.title,a.summary,a.content,a.source,...(a.tags||[])].join(' ').toLocaleLowerCase().includes(q))).sort((a,b)=>sort==='title'?a.title.localeCompare(b.title):(b.updated_at||'').localeCompare(a.updated_at||''));results.textContent=count(shown.length,'guide');list.replaceChildren();
 for(const a of shown){const c=card(a.title,a.summary||a.content.slice(0,180));c.prepend(el('div',{class:'eyebrow',text:a.category||'General'}));c.append(el('div',{class:'small',text:(a.archived?'Archived · ':'')+(a.stage==='ready'?'Ready to use':'Draft')+' · '+date(a.updated_at)}),el('p',{class:'small',text:(a.tags||[]).join(' · ')}),el('div',{class:'actions'},button('Read',()=>readGuide(a),'primary'),button('Edit',()=>editGuide(a),'','manage_guides'),button(a.archived?'Restore':'Archive',()=>action(()=>mutate('guide_archive',{id:a.id,archived:!a.archived}),a.archived?'Guide restored.':'Guide archived.'),'','manage_guides')));list.append(c);}
 if(!shown.length)list.append(empty(rows.length?'No matching guides':'Start your Guides collection',rows.length?'Try another category, status or search.':'Add a how-to, reference or procedure for other operators to use.',button('New guide',()=>editGuide(),'','manage_guides')));
 }
 render();
}
function helpLayout(title,intro){
 heading(title,intro);
 const nav=el('nav',{class:'help-topics','aria-label':'Help topics'}),body=el('div',{class:'help-body stack'});
 nav.append(search('Find a Help topic…',q=>{for(const a of nav.querySelectorAll('a'))a.hidden=!a.textContent.toLocaleLowerCase().includes(q);}));
 for(const t of helpTopics){const a=link(t,workspaceLink('help','&topic='+encodeURIComponent(t)),'');if(t===topic)a.setAttribute('aria-current','page');nav.append(a);}
 $('page-content').append(el('div',{class:'help-layout'},nav,body));return body;
}
function bugEditor(bug=null){
 const revision=store.revision;
 editor(bug?'Edit bug note':'Log a bug','Save a local note with the steps, what happened and what you expected. Nothing is sent automatically.',[
  {name:'title',label:'Short description',required:true,max:160,value:bug?.title},
  {name:'area',label:'Area',type:'select',options:['Chat','Missions','Roadmaps','Sprints','Staff','Teach','Creations','Dashboards','Settings','Other'].map(x=>[x,x]),value:bug?.area||'Other'},
  {name:'content',label:'Steps, actual result and expected result',type:'textarea',rows:10,required:true,max:20000,value:bug?.content||'Steps:\n\nWhat happened:\n\nWhat I expected:\n'}
 ],v=>mutate('bug_save',{...v,id:bug?.id},revision),{exports:(v,format)=>download({...bug,...v},v.title||'bug-note',format)});
}
function bugsPage(body){
 let mode='open',q='';const list=el('div',{class:'stack'});
 body.append(el('div',{class:'actions'},button('＋ Log bug',()=>bugEditor(),'primary','admin'),button('Export bug notes',()=>download(store.bugs,'aether-bug-notes'))),el('div',{class:'toolbar'},search('Search bug notes…',v=>{q=v;render();}),tabs('Bug status',[['open','Open'],['resolved','Resolved'],['all','All']],mode,v=>{mode=v;render();})),list);
 function render(){const rows=store.bugs.filter(b=>(mode==='all'||b.status===mode)&&(!q||(b.title+' '+b.content+' '+b.area).toLocaleLowerCase().includes(q))).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));list.replaceChildren();for(const b of rows){const c=card(b.title);c.append(el('div',{class:'small',text:b.area+' · '+date(b.updated_at)+' · '+b.status}),el('p',{class:'prose',text:b.content}),el('div',{class:'actions'},button('Edit',()=>bugEditor(b),'','admin'),button(b.status==='open'?'Mark resolved':'Reopen',()=>action(()=>mutate('bug_status',{id:b.id,status:b.status==='open'?'resolved':'open'}),'Bug status updated.'),'','admin')));list.append(c);}if(!rows.length)list.append(empty('No bug notes in this view','Keep a note when something gets in your way so it is easy to pick up later.',button('Log bug',()=>bugEditor(),'','admin')));}
 render();
}
async function logsPage(body){
 const catalogue=await projects();let current='workspace',q='',version=0,entries=store.events.map(e=>({at:e.at,kind:e.action,text:e.text}));
 const list=el('div',{class:'stack'}),note=el('p',{class:'result-note'});
 body.append(el('p',{text:'Read local workspace actions or the records of one project. Exports contain only the entries currently shown.'}),el('div',{class:'toolbar'},select('Record source',[['workspace','Workspace actions'],...catalogue.rows.map(p=>[p.id,p.name])],current,async value=>{current=value;const token=++version;note.textContent='Loading records…';try{const rows=value==='workspace'?store.events.map(e=>({at:e.at,kind:e.action,text:e.text})):(await api('/api/atelier/read?commission='+encodeURIComponent(value))).notes.map(n=>({at:n.at,kind:n.kind,sender:n.sender?.name||n.sender?.id||'',text:n.plain?.text||n.text||''}));if(token!==version)return;entries=rows;render();}catch(e){if(token===version){note.textContent=e.message;list.replaceChildren();}}}),search('Search records…',v=>{q=v;render();}),button('Export records',()=>download(filtered(),'aether-records'))),note,list);
 function filtered(){return entries.filter(e=>!q||(e.kind+' '+e.text+' '+(e.sender||'')).toLocaleLowerCase().includes(q)).slice().reverse();}
 function render(){const rows=filtered();note.textContent=count(rows.length,'record');list.replaceChildren();for(const e of rows.slice(0,200)){const c=card(e.kind.replaceAll('_',' '));c.append(el('div',{class:'small',text:(e.at||'')+(e.sender?' · '+e.sender:'')}),el('p',{class:'prose',text:e.text}));list.append(c);}if(rows.length>200)note.textContent+=' · showing the newest 200; export includes all matches';if(!rows.length)list.append(empty('No records in this view','Select a project or try another search.'));}
 render();
}
async function lexiconPage(body){
 const reference=await api('/workspace-assets/lexicon.json');let q='';const list=el('div',{class:'lexicon-results stack'}),note=el('p',{class:'result-note'});
 body.append(el('p',{text:'The canonical Aether symbolic-language reference, included with this workspace.'}),el('div',{class:'toolbar'},search('Search a glyph, term or phrase…',v=>{q=v;render();}),button('Export reference',()=>download(reference,'aether-lexicon'))),note,list);
 function render(){const rows=reference.sections.filter(s=>!q||(s.title+' '+s.content).toLocaleLowerCase().includes(q));note.textContent=count(rows.length,'section');list.replaceChildren();for(const s of rows){const details=el('details',{class:'ui-card'},el('summary',{text:s.title}),el('div',{class:'lexicon',text:s.content}));if(q)details.open=true;list.append(details);}if(!rows.length)list.append(empty('No matching reference sections','Try the glyph itself, its name or a related phrase.'));}
 render();
}
async function helpPage(){
 if(topic==='Guides'){guidesPage();return;}
 if(topic==='Log Bug'){bugsPage(helpLayout('Log Bug','Keep a useful note when something needs attention.'));return;}
 if(topic==='View Logs'){await logsPage(helpLayout('View Logs','Workspace actions and project records, in one place.'));return;}
 if(topic==='View Lexicon'){await lexiconPage(helpLayout('Aether Lexicon','Find the symbols and conventions behind the language.'));return;}
 const guide=guides[topic]||guides.About,body=helpLayout(guide.title,guide.intro);
 const c=card('Using '+guide.title),steps=el('ol');for(const text of guide.steps)steps.append(el('li',{text}));c.append(steps,el('div',{class:'actions'},...guide.links.map(([t,h])=>link(t,h))));body.append(c);
 if(topic==='About'){
  $('page-title').textContent='Help';document.title='Help · Aether Mission Control';
  const quick=el('div',{class:'grid'});for(const [title,intro,target]of [['Guides','Create, collect and read operator how-tos and reference material.','Guides'],['Chat & transforms','Explore with a model and turn useful results into artifacts.','Chat'],['Plan the work','Organize missions, projects and roadmaps.','Missions'],['Local setup','Find model connections, API keys and operator access.','Settings'],['Troubleshooting','Keep a bug note with reproduction steps and expected behavior.','Log Bug'],['Aether Lexicon','Find symbols and language conventions.','View Lexicon']]){const item=card(title,intro);item.append(link('Open '+title,workspaceLink('help','&topic='+encodeURIComponent(target))));quick.append(item);}body.prepend(quick);
  try{const health=await api('/api/health');body.append(card(health.version?'Aether v'+health.version+' · '+health.release_stage:'This local session','Edition: '+health.edition+' · started '+date(health.started_at)));}catch(_){/* The guide remains useful without diagnostics. */}
 }
}

export {helpPage};
