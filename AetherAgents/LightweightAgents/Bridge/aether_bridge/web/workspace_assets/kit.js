/* Owner: shared workspace UI primitives, session state, dialogs, exports and permissions.
 * Must never: own area-specific workflows, bypass server authorization, or persist agent memory.
 */
const $=id=>document.getElementById(id), params=new URLSearchParams(location.search);
const view=params.get('view')||'dashboard', topic=params.get('topic')||'About';
let store=null,busy=false,editorSaving=false,operator=null;
const can=p=>!!operator&&(operator.role==='admin'||operator.permissions.includes(p));
const refreshButton=$('refresh');
let renderView, beforeLoad=()=>{};
function configureWorkspace(callbacks){renderView=callbacks.render;beforeLoad=callbacks.beforeLoad;}
const el=(tag,attrs={},...children)=>{const n=document.createElement(tag);for(const [k,v]of Object.entries(attrs)){if(k==='text')n.textContent=v;else if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else if(v!==false&&v!=null)n.setAttribute(k,v===true?'':v);}for(const c of children)if(c!=null)n.append(c);return n;};
const link=(text,href,cls='button')=>el('a',{text,href,class:cls});
const button=(text,run,cls='',permission=null)=>el('button',{text,type:'button',class:cls,onclick:run,'data-permission':permission});
const date=value=>{const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});};
const count=(n,word)=>n+' '+word+(n===1?'':'s');
const projectLink=p=>'/projects/atelier?commission='+encodeURIComponent(p.id);
const workspaceLink=(v,extra='')=>'/workspace?view='+encodeURIComponent(v)+extra;
function announce(text,error=false){$('notice').textContent=text||'';$('notice').className=error?'error':'';}
async function api(url,body){const options={cache:'no-store'};if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}const r=await fetch(url,options);const v=await r.json();if(!r.ok)throw Error(v.error||'This action could not be completed.');return v;}
function heading(title,intro,actions=[]){document.title=title+' · Aether Mission Control';$('page-title').textContent=title;$('page-title').dataset.help=view;$('page-intro').textContent=intro;$('page-actions').replaceChildren(refreshButton,...actions);$('page-content').replaceChildren();window.aetherHelp?.attach($('workspace-main'));}
function card(title,intro){const c=el('section',{class:'ui-card'},el('h2',{text:title}));if(intro)c.append(el('p',{class:'muted',text:intro}));return c;}
function empty(title,intro,action){return el('section',{class:'empty'},el('h2',{text:title}),el('p',{text:intro}),action);}
function overview(items){return el('section',{class:'overview','aria-label':'Overview'},...items.map(([n,label])=>el('div',{class:'metric'},el('strong',{text:n}),el('span',{text:label}))));}
function select(label,items,value,change){const s=el('select',{'aria-label':label});for(const [id,text]of items)s.append(el('option',{value:id,text}));s.value=value;s.onchange=()=>change(s.value);return s;}
function search(label,change){const i=el('input',{type:'search',placeholder:label,'aria-label':label,autocomplete:'off'});i.oninput=()=>change(i.value.toLocaleLowerCase().trim());return el('label',{class:'search'},i);}
function tabs(label,items,value,change){const box=el('div',{class:'tabs',role:'group','aria-label':label});for(const [id,text]of items){const b=button(text,()=>{for(const other of box.children)other.setAttribute('aria-pressed',String(other===b));change(id);});b.setAttribute('aria-pressed',String(id===value));box.append(b);}return box;}
function chart(title,items,href){const c=card(title),box=el('div',{class:'bar-chart'}),max=Math.max(1,...items.map(x=>x[1]));for(const [label,n]of items){const track=el('div',{class:'bar-track','aria-hidden':'true'}),fill=el('div',{class:'bar-fill'});fill.style.width=(n/max*100)+'%';track.append(fill);box.append(el('div',{},el('div',{class:'bar-label'},el('span',{text:label}),el('strong',{text:n})),track));}if(!items.length)c.append(el('p',{class:'muted',text:'No recorded items in this view.'}));c.append(box);if(href)c.append(el('div',{class:'actions'},link('Open '+title.toLowerCase(),href)));return c;}
function download(value,name,format='json'){const escape=s=>String(s).replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&apos;'}[c]));let text,mime,ext;if(format==='text'){text=typeof value==='string'?value:value.content||JSON.stringify(value,null,2);mime='text/plain';ext='txt';}else if(format==='xml'){const xml=(k,v)=>'<'+k+'>'+(Array.isArray(v)?v.map(x=>xml('item',x)).join(''):v&&typeof v==='object'?Object.entries(v).map(([a,b])=>xml(a,b)).join(''):escape(v??''))+'</'+k+'>';text='<?xml version="1.0" encoding="UTF-8"?>\n'+xml('artifact',value);mime='application/xml';ext='xml';}else{text=JSON.stringify(value,null,2);mime='application/json';ext='json';}const url=URL.createObjectURL(new Blob([text],{type:mime+';charset=utf-8'})),a=link('',url);a.download=name.replace(/[^a-z0-9_-]+/gi,'-').slice(0,90)+'.'+ext;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function mutate(action,body={},revision=store.revision){const value=await api('/api/workspace',{action,revision,...body});store=value;return value;}
async function action(run,message){if(busy)return;busy=true;refreshButton.disabled=true;try{await run();await renderView();announce(message);}catch(e){announce(e.message,true);}finally{busy=false;refreshButton.disabled=false;}}
// Michel, 11-05: a viewer's most pressed button is Close, so it is the gold one on the lower right.
function viewerClose(){const close=$('editor-cancel');close.className='primary';$('editor-actions').append(close);}
function editor(title,intro,fields,save,{wide=false,exports=null,saveLabel='Save',readonly=false,onReady=null}={}){
 const dialog=$('editor');$('editor-cancel').textContent='Cancel';$('editor-cancel').className='';dialog.className=wide?'wide':'';$('editor-title').textContent=title;$('editor-intro').textContent=intro;$('editor-error').textContent='';$('editor-save').textContent=saveLabel;$('editor-save').hidden=readonly;$('editor-fields').replaceChildren();
 for(const f of fields){const id='edit-'+f.name;const n=f.type==='textarea'?el('textarea',{id,name:f.name,rows:f.rows||4,class:f.class||''}):f.type==='select'?select(f.label,f.options,f.value,()=>{}):el('input',{id,name:f.name,type:f.type||'text'});n.id=id;n.name=f.name;if(f.type==='checkbox')n.checked=!!f.value;if(readonly){n.readOnly=true;if(f.type==='select'||f.type==='checkbox')n.disabled=true;}if(f.type!=='select')n.value=f.value||'';if(f.type==='checkbox')n.value='on';if(f.required)n.required=true;if(f.max)n.maxLength=f.max;$('editor-fields').append(el('label',{for:id,text:f.label}),n);if(f.hint)$('editor-fields').append(el('div',{class:'small',text:f.hint}));}
 const values=()=>Object.fromEntries(new FormData($('editor-form')).entries());
 for(const input of $('editor-fields').querySelectorAll('input[type="password"]'))input.autocomplete='new-password';
 $('editor-actions').replaceChildren($('editor-cancel'),$('editor-save'));
 if(exports)for(const format of ['text','json','xml'])$('editor-actions').prepend(button('Export '+format.toUpperCase(),()=>exports(values(),format)));
 $('editor-form').onsubmit=async e=>{e.preventDefault();if(editorSaving||readonly)return;editorSaving=true;$('editor-save').disabled=true;try{await save(values());dialog.close();await load();announce('Saved.');}catch(err){$('editor-error').textContent=err.message;}finally{editorSaving=false;$('editor-save').disabled=false;}};
 if(onReady)onReady($('editor-form'));
 dialog.showModal();$('editor-fields').querySelector('input,textarea,select')?.focus();
}
$('editor-cancel').onclick=()=>{if(!editorSaving)$('editor').close();};$('editor').addEventListener('cancel',e=>{if(editorSaving)e.preventDefault();});
$('editor').addEventListener('close',()=>{for(const input of $('editor-fields').querySelectorAll('input[type="password"]'))input.value='';});
async function load(){if(busy)return;busy=true;beforeLoad();refreshButton.disabled=true;announce('');try{operator=(await api('/api/access/session')).user;store=await api('/api/workspace');await renderView();}catch(e){announce(e.message+' Use Refresh to try again.',true);if($('page-content').textContent==='Loading…')$('page-content').replaceChildren();}finally{busy=false;refreshButton.disabled=false;}}

export {$, action, announce, api, button, can, card, chart, configureWorkspace, count, date, download, editor, el, empty, heading, link, load, mutate, operator, overview, params, projectLink, refreshButton, search, select, store, tabs, topic, view, viewerClose, workspaceLink};
