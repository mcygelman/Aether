/* Own the read-only technical learning browser and explicit storage transition.
 * Never approve lessons, infer competence, edit identity or expose project history as portable learning.
 */
import {$,api,button,can,card,el,heading,link,search,select,announce} from './kit.js';
const API='/api/agent-catalog/learning';
export async function learningPage(){
 if(!document.getElementById('learning-style'))document.head.append(el('link',{id:'learning-style',rel:'stylesheet',href:'/workspace-assets/technical-learning.css'}));
 heading('Technical Learning','Specialist-owned knowledge, methods and evolving technical paths.',[link('AI Staff','/agents'),link('Specialist catalog','/workspace?view=agent-catalog')]);
 const root=$('page-content'),directory=await api(API),chosen=new URLSearchParams(location.search).get('agent');
 const id=chosen||directory.agents[0]?.id;root.classList.add('learning-view');
 root.append(el('p',{class:'banner',text:'Specialists file and revise their own technical understanding directly. References and feedback inform judgment; they are optional. Authored personality stays fixed.'}));
 root.append(select('Specialist',directory.agents.map(a=>[a.id,a.name]),id||'',v=>{location.href='/workspace?view=technical-learning&agent='+encodeURIComponent(v);}));
 if(!id){root.append(el('p',{text:'No installed specialists yet. Add one from the catalog.'}));return;}
 const data=await api(API+'?agent='+encodeURIComponent(id));
 async function transition(action){try{await api(API+'/transition',{agent:id,action});await learningPage();}catch(e){announce(e.message,true);}}
 if(data.mode==='legacy'){
  root.append(card(data.name,'This specialist still uses its original cartridge. A technical store starts fresh and retains the original cartridge and pointer for restoration. Historical project content is not imported.'));
  if(can('manage_agents'))root.append(button('Start separate technical memory',()=>transition('technical')));
  return;
 }
 root.append(el('h2',{text:data.name}),el('p',{class:'small',text:'Cartridge revision '+data.revision+' · '+data.lessons.length+' lessons · '+data.paths.length+' paths'}));
 const context=card('Project working context',data.project_context+' The active project wake offers its latest checkpoint, decisions, closing words and action history separately.');
 context.append(link('Open project teams','/projects/roster'));root.append(context);
 const names=new Map(data.paths.map(p=>[p.id,p.title]));let query='',path='';
 function pathName(id,seen=new Set()) {if(!id)return 'Unfiled';if(seen.has(id))return names.get(id)||id;seen.add(id);const p=data.paths.find(x=>x.id===id);return p?(p.parent?pathName(p.parent,seen)+' → ':'')+p.title:id;}
 const paths=card('Technical paths','The specialist can create, rename, move and cross-link these subjects.');
 for(const p of [...data.paths].sort((a,b)=>pathName(a.id).localeCompare(pathName(b.id))))paths.append(el('p',{text:pathName(p.id)+((p.related||[]).length?' · Related: '+p.related.map(x=>names.get(x)||x).join(', '):'')}));
 root.append(paths);
 const grid=el('div',{class:'learning-lessons'}),count=el('p',{role:'status'});
 function render(){grid.replaceChildren();const rows=data.lessons.filter(l=>(!path||l.path===path)&&(!query||JSON.stringify(l).toLowerCase().includes(query)));count.textContent=rows.length+' matching lessons';
  for(const l of rows){const c=card(l.title,'');c.append(el('p',{class:'small',text:pathName(l.path)}),el('p',{class:'learning-body',text:l.body||''}),el('p',{text:'Qualifications: '+(l.qualifications||'None recorded')}),el('p',{text:'References: '+((Array.isArray(l.references)?l.references.join('; '):l.references)||'None recorded')}),el('p',{text:'Related: '+((l.related||[]).map(x=>names.get(x)||data.lessons.find(r=>r.id===x)?.title||x).join(', ')||'None recorded')}));if(l.supersedes)c.append(el('p',{text:'Supersedes: '+(data.lessons.find(r=>r.id===l.supersedes)?.title||l.supersedes)}));grid.append(c);}
 }
 root.append(el('div',{class:'toolbar'},search('Search technical lessons',v=>{query=v;render();}),select('Technical path',[['','All paths'],...data.paths.map(p=>[p.id,pathName(p.id)])],path,v=>{path=v;render();})),count,grid);
 const history=el('section',{class:'ui-card'},el('h2',{text:'Revision history'}));let after=0;
 const more=button('Load revision history',async()=>{more.disabled=true;try{const h=await api(API+'/history?agent='+encodeURIComponent(id)+'&after='+after);for(const entry of h.changes){
 const changed=entry.after.length?entry.after:entry.before;
 const detail=el('details',{},el('summary',{text:'Revision '+entry.revision+' · '+(changed.map(n=>n.title).join(', ')||'Memory maintenance')}),el('p',{class:'small',text:entry.at}));
 for(const phase of ['before','after'])for(const n of entry[phase]){
  const section=el('section',{},el('h3',{text:(phase==='before'?'Before: ':'After: ')+n.title}),el('p',{class:'learning-body',text:n.body||'Technical path'}));
  if(n.parent||n.path)section.append(el('p',{text:'Path: '+pathName(n.parent||n.path)}));
  if(n.qualifications)section.append(el('p',{text:'Qualifications: '+n.qualifications}));
  if(n.references?.length)section.append(el('p',{text:'References: '+(Array.isArray(n.references)?n.references.join('; '):n.references)}));
  if(n.related?.length)section.append(el('p',{text:'Related: '+n.related.map(x=>names.get(x)||data.lessons.find(r=>r.id===x)?.title||x).join(', ')}));
  detail.append(section);
 }history.append(detail);
 }after=h.next_after;more.textContent='Load more history';more.disabled=!h.changes.length||after>=h.revision;}catch(e){announce(e.message,true);more.disabled=false;}});
 history.append(more);root.append(history);
 if(data.can_restore&&can('manage_agents')){const restore=el('details',{},el('summary',{text:'Storage compatibility'}),el('p',{text:'Restore the original cartridge pointer. Both cartridges are retained. This affects future working memory; it does not undo technical lessons.'}),button('Restore original cartridge',()=>transition('restore')));root.append(restore);}
 render();
}
