/* Owner: private human Chat dashboard, knowledge review, capture and export.
 * Must never: open agent memory, silently publish, or grant permissions through UI.
 */
import {$,api,button,card,editor,el,heading,link,download,announce,date} from './kit.js';
const endpoint='/api/model-chat/cartridge';
const kinds=[['knowledge','Knowledge'],['preference','Preference'],['decision','Decision'],['question','Open question'],['summary','Session summary']];
let directory;
const save=body=>api(endpoint,{revision:directory.revision,...body});
function editNote(note=null,source=null,content=''){
 editor(note?'Edit saved knowledge':'Save personal knowledge','Private to your account. Items enabled for recall may be sent to your selected model when relevant. Review model claims before confirming them. Saving here does not publish to Creations.',[
  {name:'title',label:'Title',required:true,max:120,value:note?.title||source?.title?.slice(0,120)||''},
  {name:'kind',label:'Type',type:'select',options:kinds,value:note?.kind||'knowledge'},
  {name:'content',label:'Content',type:'textarea',required:true,max:8000,rows:9,value:note?.content||content},
  {name:'confirmed',label:'I have reviewed and confirmed this item',type:'checkbox',value:note?.confirmed||false},
  {name:'use_in_chat',label:'Allow this item in relevant Chat context',type:'checkbox',value:note?.use_in_chat??true}
 ],v=>save({action:'save',id:note?.id,title:v.title,kind:v.kind,content:v.content,confirmed:!!v.confirmed,use_in_chat:!!v.use_in_chat,...(source?{chat_id:source.chat_id,messages:source.messages}: {})}),{wide:true,saveLabel:'Save privately'});
}
export async function chatDashboard(){
 document.body.classList.add('personal-chat-dashboard');
 if(!document.getElementById('personal-chat-style'))document.head.append(el('link',{id:'personal-chat-style',rel:'stylesheet',href:'/workspace-assets/chat-personal.css'}));
 directory=await api(endpoint);
 heading('My Chat dashboard','Your conversations and personal knowledge. Model-only Chat; heavyweight agent memory stays separate.',[
  link('New chat','/'),button('Add knowledge',()=>editNote(),'primary'),
  button('Export my cartridge',async()=>{try{download(await api(endpoint+'?export=1'),'personal-chat-cartridge');}catch(e){announce(e.message,true);}})
 ]);
 const root=$('page-content');
 root.append(el('p',{class:'small',text:'Private within this installation’s account controls. The local cartridge is not encrypted. Export includes your conversations and saved knowledge; share the file deliberately.'}));
 const stats=el('div',{class:'personal-stats'}),recallCard=card(directory.recall?'On':'Off','Personal recall');recallCard.id='personal-recall-status';
 stats.append(card(String(directory.chats.filter(c=>!c.deleted).length),'Saved conversations'),card(String(directory.notes.filter(n=>!n.archived).length),'Personal knowledge items'),recallCard);
 root.append(stats);
 const recall=el('input',{type:'checkbox',id:'personal-recall'});recall.checked=directory.recall;
 const recallLabel=el('label',{for:'personal-recall',text:'Use saved personal context in Chat'});
 recall.onchange=async()=>{recall.disabled=true;try{directory={...directory,...await save({action:'settings',recall:recall.checked})};recallCard.querySelector('h2').textContent=directory.recall?'On':'Off';announce('Personal recall '+(recall.checked?'enabled.':'disabled.'));}catch(e){recall.checked=!recall.checked;announce(e.message,true);}finally{recall.disabled=false;}};
 root.append(el('div',{class:'personal-toggle'},recall,recallLabel),el('p',{class:'small',text:'Preferences, summaries for the current chat and relevant saved items can be selected, up to six within the context budget. Unconfirmed items remain labeled. Turn recall off here for every chat, or in a conversation for that chat only. No automatic knowledge extraction or background model calls.'}));
 const search=el('input',{type:'search',placeholder:'Search chats and saved knowledge', 'aria-label':'Search chats and saved knowledge'});
 const archived=el('input',{type:'checkbox',id:'personal-archived'});
 root.append(search,el('div',{class:'personal-toggle'},archived,el('label',{for:'personal-archived',text:'Include archived knowledge and deleted chats'})));
 const chats=el('div',{class:'grid'}),notes=el('div',{class:'grid'});
 root.append(el('h2',{text:'Resume a conversation'}),chats,el('h2',{text:'What Chat can remember'}),notes);
 const draw=()=>{
  const q=search.value.toLowerCase();chats.replaceChildren();notes.replaceChildren();
  for(const c of directory.chats.filter(c=>(archived.checked||!c.deleted)&&(c.title+' '+c.tags.join(' ')).toLowerCase().includes(q))){
   const item=card(c.title,(c.deleted?'Deleted · ':'')+date(c.updated_at));item.append(link('Open conversation','/?chat='+encodeURIComponent(c.id)));chats.append(item);
  }
  if(!chats.childElementCount)chats.append(el('p',{text:'No conversations match this view.'}));
  for(const n of directory.notes.filter(n=>(archived.checked||!n.archived)&&(n.title+' '+n.content).toLowerCase().includes(q))){
   const item=card(n.title,kinds.find(k=>k[0]===n.kind)?.[1]+' · '+(n.confirmed?'Confirmed by you':'Unconfirmed')+' · '+(n.archived?'Archived':n.use_in_chat?'Available for recall':'Stored only'));
   const details=el('details',{},el('summary',{text:'Read saved content'}),el('p',{class:'personal-note-body',text:n.content}));item.append(details);
   if(n.source){item.append(link('Source conversation','/?chat='+encodeURIComponent(n.source.chat_id)),el('p',{class:'small',text:n.source.messages.length+' source messages · '+n.source.messages.map(m=>m.role==='assistant'?'Model '+(m.model||''): 'You').filter((x,i,a)=>a.indexOf(x)===i).join(', ')}));}
   item.append(el('div',{class:'actions'},button('Edit',()=>editNote(n)),button(n.archived?'Restore':'Archive',async()=>{try{await save({action:n.archived?'restore':'archive',id:n.id});await chatDashboard();}catch(e){announce(e.message,true);}})));
   notes.append(item);
  }
  if(!notes.childElementCount)notes.append(el('p',{text:'No saved knowledge yet. Add a note, or select messages in Chat and choose Save knowledge.'}));
 };
 search.oninput=draw;archived.onchange=draw;draw();
 const query=new URLSearchParams(location.search);
 if(query.get('capture')==='1'){
  const cid=query.get('chat'),ids=(query.get('messages')||'').split(',').filter(Boolean);
  const doc=await api('/api/model-chat?id='+encodeURIComponent(cid||''));
  const picked=doc.messages.filter(m=>ids.includes(m.id));
  if(!picked.length||picked.length!==new Set(ids).size)throw Error('Choose messages from your own chat before saving knowledge.');
  const content=picked.map(m=>(m.role==='user'?'You':'Model')+': '+m.content).join('\n\n');
  if(content.length>8000)throw Error('Select fewer messages: personal knowledge is limited to 8,000 characters per item.');
  history.replaceState(null,'','/workspace?view=chat-home');
  editNote(null,{chat_id:cid,title:doc.title,messages:ids},content);
 }
}
