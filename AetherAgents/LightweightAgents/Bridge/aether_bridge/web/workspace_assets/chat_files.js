/* Owner: per-chat folder selection, access display and reviewed file proposals.
 * Must never: select another account, bypass server permission checks or write agent memory. */
(()=>{'use strict';
 const style=document.createElement('link');style.rel='stylesheet';style.href='/workspace-assets/chat-files.css';document.head.append(style);
 const dialog=document.createElement('dialog');dialog.id='files-dialog';
 dialog.innerHTML=`<h2>Files for this chat</h2><p class="muted">Choose a local folder. The model can browse, search and read PDF, Word, image, text and source files directly. Images provide OCR text to Chat; the preview stays local. Text it reads is sent to your selected model provider; attaching a folder does not upload it.</p>
 <div class="folder-primary"><button id="folder-browse" class="primary">Browse for a folder…</button><button id="folder-scratchpad">Use my scratchpad</button><button id="files-close">Close</button></div><p class="muted">Browse opens a folder chooser on the computer running Aether. Your scratchpad is private to your account and shared across your chats.</p>
 <details><summary>Enter a folder path manually</summary><label>Folder path<input type="text" id="folder-path" placeholder="Choose a folder or enter its full path"></label><button id="folder-attach">Use this folder</button></details>
 <p id="folder-summary" class="muted"></p><div id="folder-options" hidden>
 <label><input id="folder-editable" type="checkbox"> Enable text-file editing — review each proposed change before applying. PDF, Word and image originals stay read only.</label>
 <label><input id="folder-retrieval" type="checkbox"> Optional local retrieval — find passages by indexed keywords</label>
 <div class="dialog-actions"><button id="folder-index">Build / refresh index</button><button id="folder-detach">Turn off file access</button></div><p id="folder-index-state" class="muted"></p>
 <label>Search files<input type="text" id="file-query" placeholder="A word or phrase"></label><div class="dialog-actions"><button id="file-search">Search current files</button><button id="file-retrieve">Retrieve passages</button><button id="file-root">Browse root</button><button id="file-up">Parent folder</button></div>
 <div class="file-grid"><div><h3 id="file-location">Folder contents</h3><div id="file-list"></div></div><div><h3 id="file-title">File preview</h3><p id="file-range" class="muted"></p><label id="file-page-label" hidden><span id="file-page-kind">PDF page</span> <select id="file-page"></select></label><p id="file-notes" class="muted"></p><img id="file-image" alt="Local image preview" hidden><pre id="file-preview">Choose a file to read it.</pre><div class="dialog-actions"><button id="file-prev" disabled>Previous lines</button><button id="file-next" disabled>Next lines</button></div><button id="file-reference" disabled>Add file reference to message</button></div></div>
 <section id="file-proposals"><h3>Proposed changes</h3><div id="file-changes"></div></section>
 </div><p id="files-feedback" role="status"></p><details id="file-skips" hidden><summary>Files that could not be read</summary><ul id="file-skip-list"></ul></details><p class="muted">PDF, Word (.docx, .docm) and images (PNG, JPEG, BMP, TIFF, WebP): up to 20 MiB; PDF and TIFF: up to 500 pages. Images and scanned PDF pages use local Windows OCR automatically (preferred installed language). OCR may misread text; check important details. Images: up to 40 megapixels; TIFF pages are read in order, with a page selector for individual scans. If OCR reaches a time or text limit, unread pages are listed; select them individually to continue. Other animated images use their first frame. WebP needs a Windows decoder. Save older .doc files as .docx first. UTF-8 text and source files: up to 256 KiB. Hidden files, credential locations, agent homes, memory folders and linked paths are excluded. Direct reading works without an index.</p>`;
 document.body.append(dialog);
 const f=id=>document.getElementById(id),make=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 let pending=false,locationPath='',preview=null,revision='',proposalSignature='';
 function feedback(text){f('files-feedback').textContent=text;}
 function pdfPage(ref){return Number(ref.locations?.[0]?.match(/(?:PDF|TIFF) page (\d+)/)?.[1])||null;}
 function skips(items=[]){f('file-skips').hidden=!items.length;f('file-skip-list').replaceChildren(...items.map(item=>make('li',item.path+' — '+item.reason)));}
 function reference(ref){if(!ref.end)return ref.path+(ref.locations?.length?' · '+ref.locations.join(', '):'')+' · no recognized text';return ref.path+' · '+(ref.locations?.length?ref.locations.join(', ')+' · ':'')+'lines '+ref.start+'–'+ref.end;}
 async function request(action,extra={}){const id=await ensureChat();return api('/files/'+action,{id,...extra});}
 async function run(fn){if(pending||busy)return;pending=true;busy=true;controls();sync();feedback('Working…');try{await fn();}catch(e){feedback(e.message);}finally{pending=false;busy=false;controls();sync();}}
 function accept(value){current=value;render();}
 function sync(){
  const folder=current?.folder,running=current?.job.status==='running';
  f('chat-folder').textContent=folder?'Working folder · '+(folder.editable?'edit proposals on':'read only'):'Working folder';
  f('chat-folder').disabled=busy||!!current?.deleted;
  f('folder-options').hidden=!folder;
  f('folder-summary').textContent=folder?folder.root+' · '+(folder.editable?'Edit proposals enabled':'Read only'):'No folder attached. Folder access is separate for each chat.';
  if(revision!==(current?.id||'')+':'+(folder?.revision||'')){
   revision=(current?.id||'')+':'+(folder?.revision||'');f('folder-path').value=folder?.root||'';
   f('folder-editable').checked=!!folder?.editable;f('folder-retrieval').checked=!!folder?.retrieval;
   locationPath='';preview=null;f('file-list').replaceChildren();f('file-preview').textContent='Choose a file to read it.';f('file-range').textContent='';f('file-notes').textContent='';f('file-image').hidden=true;f('file-image').removeAttribute('src');f('file-page-label').hidden=true;skips();
  }
  f('folder-index-state').textContent=folder?.index?folder.index.files+' files · '+folder.index.chunks+' passages · indexed '+folder.index.at+(folder.index.limited?' · partial index (limit reached)':''):'No index built. Direct browsing, search and reading are available now.';
  for(const b of dialog.querySelectorAll('button,input,select'))b.disabled=pending;
  for(const id of ['folder-browse','folder-scratchpad','folder-attach','folder-editable','folder-retrieval','folder-index'])f(id).disabled=pending||running;
  f('files-close').disabled=false;f('folder-detach').disabled=pending;
  f('file-prev').disabled=pending||!preview||preview.start<=1;
  f('file-next').disabled=pending||!preview||preview.end>=preview.total_lines;
  f('file-reference').disabled=pending||!preview;f('file-up').disabled=pending||!locationPath;
  f('file-retrieve').disabled=pending||!folder?.retrieval||!folder?.index;
  f('folder-index').disabled=pending||running||!folder?.retrieval;
  const signature=JSON.stringify(current?.file_proposals||[]);
  if(signature!==proposalSignature){proposalSignature=signature;f('file-changes').replaceChildren();
   for(const p of current?.file_proposals||[]){const box=make('article','');box.className='file-proposal';box.append(make('h4',p.path+' · '+p.status),make('pre',p.diff||'(No content difference)'));
    if(p.status==='pending'){const apply=make('button','Apply change'),dismiss=make('button','Dismiss');apply.dataset.apply='true';apply.onclick=()=>run(async()=>{accept(await request('apply',{proposal:p.id}));feedback('Saved '+p.path+'.');});dismiss.onclick=()=>run(async()=>{accept(await request('dismiss',{proposal:p.id}));feedback('Proposal dismissed.');});box.append(apply,dismiss);}f('file-changes').append(box);}
   if(!current?.file_proposals?.length)f('file-changes').textContent='When editing is enabled, ask the model to propose a change. No file is written until you choose Apply change.';
  }
  for(const b of dialog.querySelectorAll('[data-apply]'))b.disabled=pending||running||!folder?.editable;
 }
 async function browse(path=''){
  const result=await request('list',{path});locationPath=path;f('file-location').textContent=path||'Folder contents';f('file-list').replaceChildren();
  for(const item of result.entries){const b=make('button',(item.directory?'Folder · ':'')+item.name);b.onclick=()=>run(()=>item.directory?browse(item.path):read(item.path));f('file-list').append(b);}
  feedback(result.truncated?'Showing a bounded listing; open a subfolder or enter an exact path in your message.':result.entries.length+' entries.');
 }
 async function read(path,start=1,expectedHash=null,page=null){preview=await request('read',{path,start:Math.max(1,start),count:100,...(page?{page}:{})});skips();f('file-image').hidden=preview.format!=='image';f('file-image').removeAttribute('src');if(preview.format==='image')f('file-image').src='/api/model-chat/files/preview?'+new URLSearchParams({id:current.id,path,sha256:preview.sha256,...(preview.page_count?{page:preview.preview_page}:{})});f('file-title').textContent=path;f('file-range').textContent=(preview.format==='text'?'Lines ':'Extracted lines ')+preview.start+'–'+preview.end+' of '+preview.total_lines+(preview.locations?.length?' · '+preview.locations.join(', '):'');if(!preview.total_lines)f('file-range').textContent='No recognized text';f('file-notes').textContent=(preview.width?preview.width+' × '+preview.height+' pixels · ':'')+(preview.warnings||[]).join(' ');f('file-page-label').hidden=!preview.page_count;f('file-page-kind').textContent=preview.format==='image'?'TIFF page':'PDF page';f('file-page').replaceChildren();for(let n=1;n<=preview.page_count;n++){const option=make('option',String(n));option.value=n;f('file-page').append(option);}f('file-page').value=page||(preview.format==='image'?preview.preview_page:pdfPage(preview))||1;f('file-preview').textContent=preview.text?preview.text.split('\n').map((line,i)=>(i+preview.start)+'  '+line).join('\n'):'No readable text was recognized.';feedback(expectedHash&&preview.sha256!==expectedHash?'This file changed since the cited response. The preview shows its current contents.':'Read directly from the folder.');}
 async function search(retrieval){const result=await request(retrieval?'retrieve':'search',{query:f('file-query').value});skips(result.skipped);f('file-list').replaceChildren();f('file-location').textContent=retrieval?'Retrieved passages':'Search results';for(const hit of result.hits){const b=make('button',reference(hit)+' · '+hit.text.slice(0,180));b.onclick=()=>run(()=>read(hit.path,hit.format==='image'&&pdfPage(hit)?1:hit.start,hit.sha256,pdfPage(hit)));f('file-list').append(b);}feedback(result.hits.length+' matches.'+(result.limited?' Partial search/index: a reading limit was reached.':'')+(result.skipped?.length?' Some files or pages could not be read; see the details below.':'')+(result.stale?' Some indexed files changed. Refresh the index to include them.':''));}
 async function open(){if(!dialog.open)dialog.showModal();await run(async()=>{await ensureChat();sync();if(current.folder)await browse();else feedback('Choose a folder to begin.');});}
 f('chat-folder').onclick=open;f('files-close').onclick=()=>dialog.close();
 f('folder-browse').onclick=()=>run(async()=>{feedback('The Windows folder chooser is open on the computer running Aether. Choose a folder or cancel there to return.');const result=await request('choose');if(result.path){accept(await request('attach',{path:result.path}));await browse();}else feedback('No folder chosen.');});
 f('folder-scratchpad').onclick=()=>run(async()=>{accept(await request('scratchpad'));await browse();});
 f('folder-attach').onclick=()=>run(async()=>{accept(await request('attach',{path:f('folder-path').value.trim()}));await browse();});
 for(const id of ['folder-editable','folder-retrieval'])f(id).onchange=()=>run(async()=>{try{accept(await request('permissions',{editable:f('folder-editable').checked,retrieval:f('folder-retrieval').checked}));feedback('Folder access updated.');}finally{revision='';}});
 f('folder-detach').onclick=()=>run(async()=>{accept(await request('detach'));feedback('Folder access removed. Any active response has been stopped. Existing chat text is retained.');});
 f('folder-index').onclick=()=>run(async()=>{const result=await request('index');accept(result.chat);skips(result.index.skipped);feedback('Local index ready.'+(result.index.skipped?.length?' Some files or pages could not be read; see the details below.':'')+' No file content was sent to a model to build it.');});
 f('file-root').onclick=()=>run(()=>browse());f('file-up').onclick=()=>run(()=>browse(locationPath.split('/').slice(0,-1).join('/')));
 f('file-search').onclick=()=>run(()=>search(false));f('file-retrieve').onclick=()=>run(()=>search(true));
 f('file-query').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();run(()=>search(false));}};
 f('file-prev').onclick=()=>run(()=>read(preview.path,Math.max(1,preview.start-100),null,preview.selected_page||null));f('file-next').onclick=()=>run(()=>read(preview.path,preview.end+1,null,preview.selected_page||null));
 f('file-page').onchange=()=>run(()=>read(preview.path,1,null,Number(f('file-page').value)));
 f('file-reference').onclick=()=>{f('text').value+=(f('text').value?'\n':'')+'Please read '+reference(preview)+'. ';resize();dialog.close();f('text').focus();};
 window.chatFilesChanged=()=>{sync();const folder=current?.folder;document.getElementById('chat-folder-status').textContent=folder?((folder.kind==='scratchpad'?'My scratchpad':'Selected folder')+' · Browse, read and search'+(folder.editable?' · Edits require approval':' · Read only')+(folder.retrieval?' · Retrieval enabled':'')+(folder.kind==='scratchpad'?'':' · '+folder.root)):current?'File access is off. Choose Files to enable it.':'Personal scratchpad available when you start a chat.';const rows=document.querySelectorAll('#thread .message');for(let i=0;i<(current?.messages.length||0);i++){const m=current.messages[i],row=rows[i];if(!row||!m.sources?.length||row.querySelector('.file-sources'))continue;const detail=make('details','');detail.className='file-sources';detail.append(make('summary','Files used · '+m.sources.length));for(const ref of m.sources){const b=make('button',reference(ref));b.onclick=async()=>{if(!current?.folder){showStatus('Attach the original folder to inspect this reference.');return;}dialog.showModal();await run(()=>read(ref.path,ref.format==='image'&&pdfPage(ref)?1:ref.start,ref.sha256,pdfPage(ref)));};detail.append(b);}row.lastElementChild.append(detail);}
  const activity=current?.job.file_activity||[];if(current?.job.status==='running'&&activity.length)showStatus('Working with files · '+activity.length+' operations · '+activity.at(-1).tool+(activity.at(-1).path?' · '+activity.at(-1).path:''));};
 window.chatFilesChanged();
})();
