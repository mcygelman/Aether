/* Owner: Chat's personal-context controls and explicit selection handoff.
 * Must never: mutate agent memory, publish, or infer that a model claim is confirmed.
 */
const chat=window.aetherChat;
const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
const toolbar=document.querySelector('.chat-context-controls');
const dashboard=make('a','My dashboard');dashboard.href='/workspace?view=chat-home';
const capture=make('button','Save selected messages to my knowledge');capture.id='save-knowledge';
const context=make('button','System prompt');context.id='personal-context';
const knowledge=document.getElementById('chat-more-knowledge');
if(knowledge){const heading=make('p','Your knowledge');knowledge.append(heading,capture,dashboard);toolbar.append(context);}else toolbar.append(dashboard,capture,context);
const style=make('link');style.rel='stylesheet';style.href='/workspace-assets/chat-personal.css';document.head.append(style);
capture.onclick=()=>{
 const doc=chat.current(),ids=chat.selected();
 if(!doc||!ids.length){chat.status('Select the messages you want to keep, then choose More, then Save selected messages to my knowledge.');return;}
 if(ids.length>100){chat.status('Select at most 100 messages.');return;}
 window.open('/workspace?view=chat-home&capture=1&chat='+encodeURIComponent(doc.id)+'&messages='+encodeURIComponent(ids.join(',')),'_blank','noopener');
};
const dialog=make('dialog');dialog.id='personal-context-dialog';
const title=make('h2','System prompt'),intro=make('p','A preview of what the model receives before your message: the briefing for the current draft and selected model. Saved context is private to your account, but included items are sent to that model. This preview can change if you edit the draft or saved knowledge.');
const label=make('label'),enabled=make('input');enabled.type='checkbox';enabled.id='chat-recall';label.htmlFor=enabled.id;label.append(enabled,document.createTextNode(' Use personal recall in this conversation'));
const status=make('p'),body=make('pre');body.className='personal-briefing';
const last=make('details'),lastTitle=make('summary','Last request briefing'),lastBody=make('pre');lastBody.className='personal-briefing';last.append(lastTitle,lastBody);
const close=make('button','Close');close.onclick=()=>dialog.close();
dialog.append(title,intro,label,status,body,last,close);document.body.append(dialog);
async function readContext(){
 const doc=chat.current();
 const r=await fetch('/api/model-chat/cartridge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'briefing',chat:doc?.id||'',query:document.getElementById('text').value,model:document.getElementById('model').value})});const v=await r.json();if(!r.ok)throw Error(v.error);
 enabled.checked=doc?.personal_recall!==false;enabled.disabled=doc?.job?.status==='running';
 status.textContent=(v.enabled?'Personal recall on':'Personal recall off')+' · '+v.items.length+' saved items selected'+(v.omitted_eligible?' · '+v.omitted_eligible+' eligible items omitted for limits':'');
 body.textContent=v.instructions;
 last.hidden=!doc?.job?.personal_context;lastBody.textContent=doc?.job?.personal_context?.instructions||'';
}
context.onclick=async()=>{try{await readContext();dialog.showModal();}catch(e){chat.status(e.message);}};
enabled.onchange=async()=>{enabled.disabled=true;try{await chat.ensure();await chat.mutate('personal-recall',{enabled:enabled.checked});await readContext();}catch(e){status.textContent=e.message;}finally{enabled.disabled=chat.current()?.job?.status==='running';}};
