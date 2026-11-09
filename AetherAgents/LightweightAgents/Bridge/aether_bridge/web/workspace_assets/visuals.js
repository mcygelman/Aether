/* Portable illustrations. SVG is an image resource, never inserted as page markup. */
(() => {
'use strict';
const MAX=512*1024, SVG='image/svg+xml';
const formats={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif',svg:SVG};
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
const button=(text,run)=>{const b=node('button',text);b.type='button';b.onclick=run;return b;};
let urls=new Set();
function release(){for(const url of urls)URL.revokeObjectURL(url);urls.clear();}
document.getElementById('editor').addEventListener('close',()=>{if(!document.getElementById('editor').open)release();});
function urlFor(blob){const url=URL.createObjectURL(blob);urls.add(url);return url;}
function svgSource(content){
 const text=String(content||'').trim();
 // An XML declaration and comments may come first (agents' SVG files often open with notes).
 const start=/^(?:<\?xml[\s\S]*?\?>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg(?:\s|>)/i;
 if(start.test(text))return text;
 for(const match of text.matchAll(/```(?:svg|xml)?\s*\n([\s\S]*?)```/gi))if(start.test(match[1].trim()))return match[1].trim();
 return null;
}
function svgBlob(text){
 if(/<!doctype|<!entity/i.test(text))throw Error('SVG declarations cannot be previewed. The source is still available.');
 const doc=new DOMParser().parseFromString(text,'image/svg+xml'),root=doc.documentElement;
 if(doc.querySelector('parsererror')||root.localName!=='svg'||(root.namespaceURI&&root.namespaceURI!=='http://www.w3.org/2000/svg'))throw Error('This SVG could not be previewed. Check its source.');
 if(!root.namespaceURI)root.setAttribute('xmlns','http://www.w3.org/2000/svg');
 // The resulting blob is used ONLY by <img>; scripts, links and external resources
 // cannot interact with the application in the browser's SVG image mode.
 return new Blob([new XMLSerializer().serializeToString(root)],{type:SVG});
}
function visualBlob(visual){
 if(!visual||!Object.values(formats).includes(visual.media_type)||typeof visual.data!=='string'||visual.data.length>699052)throw Error('This image attachment cannot be previewed.');
 const bytes=Uint8Array.from(atob(visual.data),c=>c.charCodeAt(0));
 if(!bytes.length||bytes.length>MAX)throw Error('Choose an image no larger than 512 KiB.');
 return visual.media_type===SVG?svgBlob(new TextDecoder('utf-8',{fatal:true}).decode(bytes)):new Blob([bytes],{type:visual.media_type});
}
async function fromFile(file){
 const media_type=formats[file.name.split('.').pop().toLowerCase()];
 if(!media_type)throw Error('Choose a PNG, JPEG, WebP, GIF or SVG image.');
 if(!file.size||file.size>MAX)throw Error('Choose an image no larger than 512 KiB.');
 const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('The image could not be read.'));reader.readAsDataURL(file);});
 const visual={name:file.name.slice(0,160),media_type,data,alt:file.name.replace(/\.[^.]+$/,'').slice(0,500)};
 const blob=visualBlob(visual),url=URL.createObjectURL(blob);
 try{await new Promise((resolve,reject)=>{const img=new Image();img.onload=resolve;img.onerror=()=>reject(Error('The image could not be decoded. Choose a valid image file.'));img.src=url;});}finally{URL.revokeObjectURL(url);}
 return visual;
}
function sourceDownload(content,title,type){
 const a=node('a');a.href=urlFor(new Blob([content],{type:type==='html'?'text/html;charset=utf-8':SVG}));a.download=(title||'creation').replace(/[^a-z0-9_-]+/gi,'-').slice(0,90)+'.'+type;document.body.append(a);a.click();a.remove();
}
function htmlPreview(content,title){
 // Template contents are inert during preparation. The displayed document has an
 // opaque origin, no sandbox permissions, no scripts and no network resources.
 const template=document.createElement('template');template.innerHTML=content;
 for(const n of template.content.querySelectorAll('script,iframe,frame,frameset,object,embed,base,meta,link,noscript,template'))n.remove();
 for(const n of template.content.querySelectorAll('*')){
  for(const attr of [...n.attributes]){
   const name=attr.name.toLowerCase(),value=attr.value.trim();
   if(name.startsWith('on')||['srcset','ping','action','formaction','target','download','srcdoc','autofocus'].includes(name))n.removeAttribute(attr.name);
   if(['href','xlink:href','src'].includes(name)&&!value.startsWith('#')&&!/^data:image\/(?:png|jpeg|gif|webp|svg\+xml)[;,]/i.test(value))n.removeAttribute(attr.name);
  }
  // Links to data documents and form submission are not preview interactions.
  if(n.localName==='a'){n.removeAttribute('href');n.removeAttribute('xlink:href');}
  if(['input','button','textarea','select'].includes(n.localName))n.setAttribute('disabled','');
 }
 const policy="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
 const frame=node('iframe');frame.title=(title||'Creation')+' — HTML preview';frame.setAttribute('sandbox','');frame.referrerPolicy='no-referrer';frame.className='html-preview-frame';
 frame.srcdoc='<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+policy+'"><style>html{color-scheme:light}body{margin:16px;font-family:system-ui,sans-serif;overflow-wrap:anywhere}img,svg,video{max-width:100%}*{box-sizing:border-box}</style></head><body>'+template.innerHTML+'</body></html>';
 const box=node('section',null,'html-preview'),controls=node('div',null,'visual-controls'),size=node('select');size.setAttribute('aria-label','HTML preview width');
 for(const [value,label]of [['100%','Full width'],['390px','Phone width'],['768px','Tablet width']]){const o=node('option',label);o.value=value;size.append(o);}
 size.onchange=()=>{frame.style.width=size.value;};controls.append(size,button('Download HTML',()=>sourceDownload(content,title,'html')));
 box.append(controls,node('p','Static preview: scripts, forms and external resources are disabled. Download HTML keeps the original source.','small'),frame);return box;
}
function preview(content,visual,title,kind='note'){
 if(kind==='html'){const box=htmlPreview(content,title);if(visual)box.append(preview('',visual,title));return box;}
 const svg=svgSource(content);if(!visual&&!svg)return null;
 const box=node('section',null,'visual-preview');box.setAttribute('aria-label','Visual preview');
 try{
  const blob=visual?visualBlob(visual):svgBlob(svg),stage=node('div',null,'visual-stage'),status=node('span','Loading preview…','small');
  const img=new Image();img.alt=visual?.alt||title||'Artifact preview';img.decoding='async';
  img.onload=()=>{status.textContent=img.naturalWidth+' × '+img.naturalHeight;};img.onerror=()=>{status.textContent='Preview unavailable. The saved source is still available.';};
  let current=null;
  const reload=()=>{if(current){URL.revokeObjectURL(current);urls.delete(current);}current=urlFor(blob);img.src=current;};
  const controls=node('div',null,'visual-controls'),background=node('select');background.setAttribute('aria-label','Preview background');
  for(const [value,label]of [['light','Light background'],['dark','Dark background'],['grid','Transparency grid']]){const o=node('option',label);o.value=value;background.append(o);}
  background.onchange=()=>{stage.dataset.background=background.value;};
  const zoom=node('select');zoom.setAttribute('aria-label','Preview size');
  for(const [value,label]of [['fit','Fit to panel'],['100','100%'],['150','150%'],['200','200%']]){const o=node('option',label);o.value=value;zoom.append(o);}
  zoom.onchange=()=>{img.style.width=zoom.value==='fit'?'':(img.naturalWidth*Number(zoom.value)/100)+'px';img.style.maxWidth=zoom.value==='fit'?'100%':'none';};
  controls.append(background,zoom,button('Restart preview',reload),button(kind==='svg'&&!visual?'Download SVG':'Download image',()=>{
   if(kind==='svg'&&!visual){sourceDownload(content,title,'svg');return;}
   const extension=visual?Object.keys(formats).find(k=>formats[k]===visual.media_type):'svg';
   const a=node('a');a.href=urlFor(blob);a.download=(title||'creation').replace(/[^a-z0-9_-]+/gi,'-').slice(0,90)+'.'+extension;document.body.append(a);a.click();a.remove();
  }),status);
  stage.append(img);box.append(controls,stage);
  if(matchMedia('(prefers-reduced-motion: reduce)').matches&&(blob.type===SVG||blob.type==='image/gif')){
   const start=button('Show image (may animate)',()=>{start.remove();reload();});stage.prepend(start);status.textContent='Preview waiting for you.';
  }else reload();
 }catch(e){box.append(node('p',e.message,'form-error'));}
 return box;
}
function reader(content,visual,title,cls,kind='note'){
 let sourceText=content;
 if(visual?.media_type===SVG){try{sourceText+='\n\n[ ATTACHED SVG SOURCE ]\n'+new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(visual.data),c=>c.charCodeAt(0)));}catch(_){/* Preview displays the attachment error. */}}
 const source=node('pre',sourceText,cls),rendered=preview(content,visual,title,kind);if(!rendered)return source;
 const box=node('section',null,'artifact-reader'),bar=node('div',null,'tabs');bar.setAttribute('aria-label','Artifact view');bar.setAttribute('role','group');
 const show=(isPreview)=>{rendered.hidden=!isPreview;source.hidden=isPreview;p.setAttribute('aria-pressed',String(isPreview));s.setAttribute('aria-pressed',String(!isPreview));};
 const p=button('Preview',()=>show(true)),s=button('Text / source',()=>show(false));bar.append(p,s);box.append(bar,rendered,source);
 if(visual&&!svgSource(content)&&kind!=='html')rendered.append(node('pre',content,cls));show(true);return box;
}
function editor(initial,allowAttachment,title,kind='note'){
 let visual=initial||null,pending=false,generation=0;
 const input=document.getElementById('edit-content'),label=document.querySelector('label[for="edit-content"]');
 const box=node('section',null,'visual-editor'),bar=node('div',null,'tabs'),panel=node('div'),error=node('p',null,'form-error');error.setAttribute('role','alert');
 let showing=false;
 const refresh=()=>{panel.replaceChildren();if(showing){release();panel.append(preview(input.value,visual,document.getElementById('edit-title')?.value||title,document.getElementById('edit-kind')?.value||kind)||node('p','Add an image, use SVG source, or choose HTML Page to preview page markup.','small'));}};
 const show=value=>{showing=value;input.hidden=value;label.hidden=value;panel.hidden=!value;write.setAttribute('aria-pressed',String(!value));peek.setAttribute('aria-pressed',String(value));refresh();};
 const write=button('Edit text',()=>show(false)),peek=button('Preview',()=>show(true));bar.append(write,peek);box.append(bar);
 if(allowAttachment){
  const controls=node('div',null,'visual-attachment'),picker=node('input');picker.type='file';picker.accept=Object.keys(formats).map(x=>'.'+x).join(',');picker.hidden=true;picker.setAttribute('aria-label','Attach image file');
  const name=node('span',null,'small'),alt=node('input');alt.id='visual-description';alt.maxLength=500;alt.placeholder='Describe the image';
  const altLabel=node('label','Image description');altLabel.htmlFor=alt.id;
  const remove=button('Remove image',()=>{generation++;pending=false;visual=null;update();refresh();});
  const update=()=>{name.textContent=visual?visual.name:'PNG, JPEG, WebP, GIF or SVG · up to 512 KiB';remove.hidden=!visual;alt.hidden=!visual;altLabel.hidden=!visual;alt.value=visual?.alt||'';};
  alt.oninput=()=>{if(visual)visual={...visual,alt:alt.value};};
  picker.onchange=async()=>{const file=picker.files[0];picker.value='';if(!file)return;const attempt=++generation;pending=true;error.textContent='';try{const next=await fromFile(file);if(attempt!==generation||!box.isConnected)return;visual=next;if(!input.value.trim())input.value='Image: '+next.name;update();show(true);}catch(e){if(attempt===generation)error.textContent=e.message;}finally{if(attempt===generation)pending=false;}};
  controls.append(button('Attach image',()=>picker.click()),remove,name,picker);box.append(controls,altLabel,alt);update();
 }
 box.append(error,panel);label.before(box);show(false);
 input.addEventListener('invalid',()=>show(false));
 document.getElementById('edit-kind')?.addEventListener('change',refresh);
 return ()=>{if(pending)throw Error('Wait for the image to finish loading before saving.');return visual;};
}
window.aetherVisuals={fromFile,reader,editor,svgSource};
})();
