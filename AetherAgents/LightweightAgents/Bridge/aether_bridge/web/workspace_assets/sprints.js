/* Owner: Active Sprints view: the roadmaps' sprints joined with their runs, filters and links.
 * Must never: approve work, infer task completion, or implement another area.
 */
import {$, announce, api, card, count, el, empty, heading, link, overview, projectLink, search, select, tabs} from './kit.js';
import {projects, sprintData} from './project-data.js';
async function sprintsPage(){
 // Michel, 2026-11-03: Mission -> Roadmap -> Sprint -> Task. A roadmap's sprints are the sprints; a run is the work of
 // one. This page joins the roadmaps' own sprints with the runs, so "10 still open" on Roadmaps is visible here too.
 heading('Active Sprints','Every sprint in your roadmaps: what is working, what waits for you, and what is planned next.',[link('Open Roadmaps','/roadmaps')]);
 const root=$('page-content');let runs=[],plan=[];const missions={};
 try{const [r,p,c]=await Promise.all([api('/api/tasker/projects'),api('/api/tasker/plan'),api('/api/campaigns').catch(()=>({campaigns:[]}))]);
  runs=r.projects||[];plan=p.roadmaps||[];
  for(const m of c.campaigns||[])for(const row of m.projects||[])if(row.kind==='roadmap'&&String(row.id).startsWith('folder:'))missions[row.id.slice(7)]=m;}
 catch(e){root.append(card('Sprints unavailable',e.message));return;}
 const items=[],bound=new Set(),join=(key,id)=>key+' '+id;
 for(const v of runs){const key=v.roadmap?.key||'',step=v.roadmap?.step;if(step)bound.add(join(key,step.id));
  const waiting=['prepared','interrupted'].includes(v.execution)||v.verification?.status==='review_required'||!!v.paused_for||!!v.cap_reached;
  const group=waiting?'waiting':['running','stopping'].includes(v.execution)?'working':'finished';
  items.push({kind:'run',group,key,roadmap:v.roadmap?.name||'No roadmap',title:step?.name||v.name,subtitle:step&&step.name!==v.name?v.name:'',v,
   at:v.run?.started_at||v.created||''});}
 for(const r of plan)for(const s of r.sprints){if(s.run_record||bound.has(join(r.key,s.id)))continue;
  // An Aborted step (closing a roadmap aborts its never-started sprints, Michel 11-05) is finished, not planned.
  items.push({kind:'step',group:['Closed','Aborted'].includes(s.status)?'finished':'planned',key:r.key,roadmap:r.name,title:s.name,s,r,
   at:s.updated||''});}
 const groups=[['waiting','Waiting for you','Ready to start, paused for your answer, or waiting for your review.'],
  ['working','Working now','The lead and staff are on it.'],['planned','Planned','Sprints in your roadmaps that have not started yet.'],
  ['finished','Finished','Closed in the roadmap, or ended.']];
 const n=g=>items.filter(i=>i.group===g).length;
 root.append(overview([[n('working'),'Working now'],[n('waiting'),'Waiting for you'],[n('planned'),'Planned'],[n('finished'),'Finished']]));
 let show='now',q='',mission='all',roadmap='all';
 const missionList=[...new Map(items.filter(i=>missions[i.key]).map(i=>[missions[i.key].id,missions[i.key].name]))];
 const roadmapList=[...new Map(items.map(i=>[i.key,i.roadmap]))];
 const results=el('p',{class:'result-note','aria-live':'polite'}),list=el('div');
 root.append(el('div',{class:'toolbar'},search('Search sprints, roadmaps and leads…',v=>{q=v.toLocaleLowerCase();render();}),
  tabs('Show',[['now','Now and next'],['planned','Planned'],['finished','Finished'],['all','All']],show,v=>{show=v;render();}),
  select('Mission',[['all','All missions'],...missionList],mission,v=>{mission=v;render();}),
  select('Roadmap',[['all','All roadmaps'],...roadmapList],roadmap,v=>{roadmap=v;render();})),results,list);
 function itemCard(i){const m=missions[i.key],c=card(i.title);c.prepend(el('div',{class:'eyebrow',text:(m?m.name+' · ':'')+i.roadmap}));
  if(i.kind==='run'){const v=i.v,tasks=v.tasks||[],closed=tasks.filter(t=>t.status==='Closed').length;
   c.append(el('span',{class:'pill'+(i.group==='working'?' active':i.group==='waiting'||v.terminal_failure?' warning':''),text:v.headline||v.execution}));
   if(i.subtitle)c.append(el('p',{class:'small',text:'Sprint: '+i.subtitle}));
   c.append(el('p',{text:'Lead: '+(v.lead_name||v.overseer||'—')+((v.staff_names||[]).length?' · Staff: '+v.staff_names.join(', '):'')+' · '+count(tasks.length,'task')+(tasks.length?', '+closed+' closed':'')}),
    el('div',{class:'actions'},link('View sprint','/projects/tasker?project='+encodeURIComponent(v.project_id),'button'+(i.group==='waiting'?' primary':''))));}
  else{const s=i.s;c.append(el('span',{class:'pill'+(s.status==='Closed'?' good':''),text:s.status==='Closed'?'Closed in the roadmap':s.status==='Aborted'?'Aborted in the roadmap':'Planned'}));
   const text=(s.next||s.description||'').split('\n')[0];if(text)c.append(el('p',{text:text.length>260?text.slice(0,257)+'…':text}));
   const facts=[s.owner&&'Owner: '+s.owner,s.updated&&'Updated '+s.updated].filter(Boolean).join(' · ');if(facts)c.append(el('p',{class:'small',text:facts}));
   const actions=el('div',{class:'actions'});
   if(i.group==='planned'&&!i.r.done)actions.append(link('Start sprint','/projects/tasker?roadmap='+encodeURIComponent(i.key)+'&step='+encodeURIComponent(s.id),'button primary'));
   actions.append(link('View roadmap','/roadmaps'));c.append(actions);}
  return c;}
 function render(){list.replaceChildren();let shown=0;
  const wanted={now:['waiting','working','planned'],planned:['planned'],finished:['finished'],all:['waiting','working','planned','finished']}[show];
  for(const [g,title,intro] of groups){if(!wanted.includes(g))continue;
   const these=items.filter(i=>i.group===g&&(mission==='all'||missions[i.key]?.id===mission)&&(roadmap==='all'||i.key===roadmap)&&
    (!q||[i.title,i.subtitle,i.roadmap,missions[i.key]?.name,i.v?.lead_name,i.s?.description].join(' ').toLocaleLowerCase().includes(q)));
   // Michel, 11-05: newest first (when it started, or was prepared; a roadmap's own record by its update day).
   // Planned keeps the roadmap's plan order, step 1 before step 2.
   if(g!=='planned')these.sort((a,b)=>b.at.localeCompare(a.at));
   if(!these.length)continue;shown+=these.length;
   list.append(el('h2',{text:title+' ('+these.length+')'}),el('p',{class:'small',text:intro}),el('div',{class:'grid'},...these.map(itemCard)));}
  results.textContent=count(shown,'sprint');
  if(!shown)list.append(empty(items.length?'No sprints in this view':'No sprints yet',items.length?'Try another filter.':'Add sprints to a roadmap, or start one from it on the Roadmaps page.',link('Open Roadmaps','/roadmaps')));}
 render();
}
// Michel, 2026-11-03: the old Atelier board is hidden from Active Sprints; its code stays, uncalled.
async function atelierBoard(){
 const catalogue=await projects(),data=await sprintData(catalogue);let mode='active',q='',mission='all',sort='project';
 const root=$('page-content'),results=el('p',{class:'result-note','aria-live':'polite'}),list=el('div',{class:'grid'});
 root.append(overview([[data.rows.filter(s=>s.active).length,'Active sprints'],[data.rows.filter(s=>!s.approved&&!s.closed).length,'Planned sprints'],[data.rows.filter(s=>s.active).reduce((n,s)=>n+s.tasks.length,0),'Tasks in active sprints']]));
 root.append(el('div',{class:'toolbar'},search('Search sprints, roadmaps and people…',v=>{q=v;render();}),tabs('Sprint status',[['active','Active'],['planned','Planned'],['closed','Closed'],['all','All']],'active',v=>{mode=v;render();}),select('Mission',[['all','All missions'],...catalogue.missions.map(m=>[m.id,m.name])],mission,v=>{mission=v;render();}),select('Sort sprints',[['project','By roadmap'],['name','Sprint name']],sort,v=>{sort=v;render();})));
 root.append(results,list,el('p',{class:'small',text:'Active means approved work that is not yet accepted. Task completion is not inferred from approval.'}));
 if(data.failures.length)announce('Could not read: '+data.failures.join(', ')+'. Refresh to try again.',true);
 function render(){const shown=data.rows.filter(s=>(mode==='all'||(mode==='active'?s.active:mode==='planned'?!s.approved&&!s.closed:s.closed))&&(mission==='all'||s.project.mission?.id===mission)&&(!q||(s.title+' '+s.project.name+' '+s.tasks.map(t=>t.title+' '+(t.who||'')).join(' ')).toLocaleLowerCase().includes(q))).sort((a,b)=>sort==='name'?a.title.localeCompare(b.title):a.project.name.localeCompare(b.project.name)||a.number-b.number);results.textContent=count(shown.length,'sprint');list.replaceChildren();for(const s of shown){const c=card('Sprint '+s.number+' · '+s.title);c.prepend(el('div',{class:'eyebrow',text:s.project.mission?.name||'No mission assigned'}));c.append(el('p',{},link(s.project.name,projectLink(s.project),'')),el('span',{class:'pill '+(s.active?'active':''),text:s.closed?'Accepted':s.active?'Active':'Planned'}));const details=el('details',{},el('summary',{text:count(s.tasks.length,'task')+' · view work'})),ul=el('ul',{class:'task-list'});for(const task of s.tasks)ul.append(el('li',{},document.createTextNode(task.title),task.who?el('span',{class:'owner',text:' · '+task.who}):null));details.append(ul);c.append(details,el('div',{class:'actions'},link('Open',projectLink(s.project)),link('Roadmap & records','/projects/campaign?id='+encodeURIComponent(s.project.id))));list.append(c);}if(!shown.length)list.append(empty('No '+(mode==='active'?'active ':'')+'sprints in this view','Try another filter or open a roadmap to continue planning.',link('Open Roadmaps','/roadmaps')));}
 render();
}

export {sprintsPage};
