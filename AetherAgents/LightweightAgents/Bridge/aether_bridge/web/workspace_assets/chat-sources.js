/* Own Chat source selection and links to real report results.
 * Never grant source access, inject credentials, or reconstruct result rows from model prose.
 */
const chat=window.aetherChat;
const make=(tag,text)=>{const e=document.createElement(tag);if(text)e.textContent=text;return e;};
const button=make('button','Data sources');button.type='button';button.id='chat-sources';
(document.getElementById('chat-more-sources')||document.querySelector('.toolbar')).append(button);
const style=make('link');style.rel='stylesheet';style.href='/workspace-assets/data-sources.css';document.head.append(style);
const dialog=make('dialog');dialog.className='source-picker';document.body.append(dialog);
async function request(url,body){const r=await fetch(url,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const value=await r.json();if(!r.ok)throw Error(value.error||'Source request failed.');return value;}
button.onclick=async()=>{try{
 await chat.ensure();const data=await request('/api/data-sources?model=1'),current=chat.current();dialog.replaceChildren(make('h2','Sources for this chat'),make('p','Automatic selection lets the model choose among your authorized sources. Source metadata and results may be sent to the selected model.'));
 const automatic=make('input');automatic.type='checkbox';automatic.checked=current.selected_sources==null;const autoLabel=make('label');autoLabel.append(automatic,document.createTextNode(' Use any source available to me'));dialog.append(autoLabel);
 const choices=[];
 for(const source of data.sources){const check=make('input');check.type='checkbox';check.checked=current.selected_sources?.includes(source.id)||false;const label=make('label');label.append(check,document.createTextNode(' '+source.name+' (@'+source.alias+')'));dialog.append(label,make('p',source.description));
  const mention=make('button','Mention @'+source.alias);mention.type='button';mention.onclick=()=>{document.getElementById('text').value+=' @'+source.alias+' ';dialog.close();document.getElementById('text').focus();};dialog.append(mention);choices.push({source,check});}
 if(!data.sources.length)dialog.append(make('p','No sources are enabled for your role and model use. Ask a source manager to configure access.'));
 const actions=make('div');actions.className='source-picker-actions';const save=make('button','Save selection'),close=make('button','Close'),reports=make('a','Open reports');reports.href='/workspace?view=data-sources';
 save.onclick=async()=>{try{if(await chat.mutate('data-sources',{sources:automatic.checked?null:choices.filter(c=>c.check.checked).map(c=>c.source.id)}))dialog.close();}catch(e){chat.status(e.message);}};
 close.onclick=()=>dialog.close();actions.append(save,close,reports);dialog.append(actions);dialog.showModal();
}catch(e){chat.status(e.message);}};
function results(){const current=chat.current();if(!current)return;for(const [i,message]of current.messages.entries()){if(!message.report_runs?.length)continue;const row=document.querySelectorAll('#thread article.message')[i];if(!row||row.querySelector('.source-result-links'))continue;const links=make('div');links.className='source-result-links';for(const id of message.report_runs){const a=make('a','Open report table and exports');a.href='/workspace?view=data-sources&run='+encodeURIComponent(id);links.append(a);}row.querySelector('.body')?.after(links);}}
new MutationObserver(results).observe(document.getElementById('thread'),{childList:true,subtree:false});
results();
const selected=new URLSearchParams(location.search).get('source');
if(selected)(async()=>{try{await chat.ensure();await chat.mutate('data-sources',{sources:[selected]});chat.status('Source selected. Ask what you want to see.');}catch(e){chat.status(e.message);}})();
