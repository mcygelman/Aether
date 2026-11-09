/* Own Chat's agent choice for Admins (Michel, 2026-11-05): talk with a heavyweight instead of the model alone, and end
 * the conversation so the agent files its own closing words. Never shown to other accounts; never writes memory itself.
 */
const chat=window.aetherChat,$=id=>document.getElementById(id);
const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
const label=make('label','Talk with','sr-only');label.htmlFor='agent';
const pick=make('select');pick.id='agent';pick.setAttribute('aria-label','Talk with');pick.hidden=true;
const end=make('button','End conversation');end.type='button';end.id='end-conversation';end.hidden=true;
$('model').after(label,pick);$('history').after(end);
async function load(){
 let agents=[];
 try{const r=await fetch('/api/model-chat?agents=1',{cache:'no-store'});const v=await r.json();if(r.ok)agents=v.agents||[];}catch(_){/* Chat stays model-only. */}
 const only=make('option','Model only');only.value='';pick.replaceChildren(only);
 for(const a of agents){const o=make('option',a.name+' · '+a.where+(a.busy?' (busy)':''));o.value=a.key;pick.append(o);}
 pick.hidden=!agents.length;sync(chat.current());
}
function sync(current){
 const agent=current?.agent,running=current?.job?.status==='running';
 if(agent&&![...pick.options].some(o=>o.value===agent.key)){const o=make('option',agent.name);o.value=agent.key;pick.append(o);}
 pick.value=agent?.key||'';pick.disabled=!!current?.messages.length||running;
 end.hidden=!agent||!!current?.agent_closed||!current?.messages.length;end.disabled=running;
 const heading=document.querySelector('.chat-heading .muted');
 if(heading)heading.textContent=agent?'Conversation with '+agent.name+(current.agent_closed?' · ended; its closing words are in its memory':''):'Conversation with a model';
 if(agent&&!current.tags.length)$('tags').textContent='Talking with '+agent.name+' · it manages its own memory during the conversation; End saves its closing words';
}
pick.onchange=async()=>{
 if(chat.current()?.messages.length){sync(chat.current());return;}
 try{await chat.ensure();await chat.mutate('agent',{agent:pick.value||null});}catch(e){chat.status(e.message);}
 sync(chat.current());
};
end.onclick=async()=>{
 const current=chat.current();if(!current?.agent)return;
 if(!$('model').value){chat.status('Choose a model connection first.');return;}
 if(!confirm('End this conversation with '+current.agent.name+'? '+current.agent.name+' writes its closing words into its own memory, and this chat stays as a record.'))return;
 await chat.mutate('end',{model:$('model').value});
};
window.chatAgentChanged=sync;
load();
