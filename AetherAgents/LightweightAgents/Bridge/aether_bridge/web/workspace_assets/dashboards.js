/* Owner: dashboard summaries, presentation settings and links to source work.
 * Must never: invent progress, alter source records, or eagerly load artifact editors.
 */
import {$, announce, api, button, card, chart, count, date, editor, el, empty, heading, link, mutate, overview, params, projectLink, select, store, workspaceLink} from './kit.js';
import {projects, sprintData} from './project-data.js';
import {kinds, libraryData} from './artifact-data.js';
async function readCreation(record){return (await import('./creations.js')).readCreation(record);}
function dashboardList(title,items,emptyText,href,label){
 const c=card(title),list=el('ul',{class:'list'});for(const item of items.slice(0,6))list.append(el('li',{},item));if(!items.length)c.append(el('p',{class:'muted',text:emptyText}));c.append(list);if(items.length>6)c.append(el('p',{class:'small',text:'Showing 6 of '+items.length+'. Open the full view for all records.'}));if(href)c.append(el('div',{class:'actions'},link(label||'Open full view',href)));return c;
}
const dashboardProject=p=>el('div',{},el('div',{class:'row'},link(p.name,projectLink(p),''),el('span',{class:'pill',text:p.stage||'Planning'})),el('div',{class:'small',text:(p.mission?.name||'No mission assigned')+' · '+date(p.last_at)}));
async function dashboardPage(){
 const areas=['Missions','Roadmaps','Sprints','AI Staff','Teach Frames','Creations'],requested=params.get('area')||'Missions',area=areas.includes(requested)?requested:'Missions';
 const destination={Missions:'/missions',Roadmaps:'/roadmaps',Sprints:workspaceLink('sprints'),'AI Staff':'/agents','Teach Frames':workspaceLink('teach'),Creations:workspaceLink('creations')}[area];
 const intro={Missions:'See where projects stand and choose the work to open next.',Roadmaps:'See which projects have a plan and where planning still needs attention.',Sprints:'See active work, planned sprints and recorded task ownership.','AI Staff':'See availability, current project assignments and agents needing attention.','Teach Frames':'See teaching drafts and material marked ready to share.',Creations:'See useful results from Chat and the workspace, ready to refine and share.'}[area];
 const presentation=store.dashboards?.[area]||{};
 heading(presentation.title||area+' dashboard',presentation.notes||intro,[link('Open '+area,destination),button('Edit dashboard',()=>{const revision=store.revision;editor('Edit '+area+' dashboard','Set the title and notes everyone sees. Counts continue to come from workspace records.',[{name:'title',label:'Title',required:true,max:120,value:presentation.title||area+' dashboard'},{name:'notes',label:'Dashboard notes',type:'textarea',max:4000,value:presentation.notes||''}],v=>mutate('dashboard_save',{...v,area},revision));},'','manage_dashboards')]);
 const root=$('page-content');root.append(el('nav',{class:'dashboard-nav','aria-label':'Dashboard views'},...areas.map(a=>link(a,workspaceLink('dashboard','&area='+encodeURIComponent(a)),'button'+(a===area?' selected':'')))));for(const a of root.querySelectorAll('.dashboard-nav a.selected'))a.setAttribute('aria-current','page');
 const stamp=el('p',{class:'small',text:'Loading current records…'});root.append(stamp);const body=el('div');root.append(body);
 if(area==='AI Staff'){
  const results=await Promise.allSettled([api('/api/agent-availability'),api('/api/campaign-roster?staff=1')]);if(results[0].status!=='fulfilled')throw Error('Agent availability could not be read. Use Refresh or open AI Staff.');
  const rows=results[0].value.agents||[],staff=results[1].status==='fulfilled'?results[1].value:null,projects=staff?.projects||[],complete=!!staff&&!projects.some(p=>p.error),assigned=new Set(projects.flatMap(p=>(p.members||[]).map(m=>m.agent_id))),unassigned=rows.filter(a=>a.status==='available'&&!assigned.has(a.id));
  body.append(overview([[rows.length,'Registered agents'],[rows.filter(a=>a.status==='available').length,'Available here'],[rows.filter(a=>a.working).length,'Working now'],[complete?unassigned.length:'—','Available · no project']]));
  if(!complete)body.append(el('div',{class:'banner warning',text:'Some project assignments could not be read. Assignment totals are incomplete; open AI Staff to review.'}));
  const agentRow=a=>el('div',{},el('div',{class:'row'},link(a.name,'/agents',''),el('span',{class:'pill',text:a.weight==='heavyweight'?'Heavyweight':a.weight==='lightweight'?'Lightweight':'Type unspecified'})),el('div',{class:'small',text:({available:'Available',ejected:'Not available here',ejection_pending:'Finishing a turn',unavailable:'Folder unavailable'})[a.status]||a.status}));
  const assignmentRows=projects.filter(p=>!p.error&&(p.members||[]).length).map(p=>el('div',{},link(p.title||p.id,'/projects/roster?commission='+encodeURIComponent(p.id),''),el('div',{class:'small',text:count(p.members.length,'assigned agent')})));
  body.append(el('div',{class:'grid'},chart('Availability',[['Available',rows.filter(a=>a.status==='available').length],['Not available',rows.filter(a=>a.status==='ejected').length],['Finishing a turn',rows.filter(a=>a.status==='ejection_pending').length],['Folder unavailable',rows.filter(a=>a.status==='unavailable').length]],'/agents'),dashboardList('Working now',rows.filter(a=>a.working).map(agentRow),'No agent is reported as working here.','/agents','Open AI Staff'),dashboardList('Availability to review',rows.filter(a=>['unavailable','ejection_pending'].includes(a.status)).map(agentRow),'No missing folders or pending availability changes.','/agents','Manage agents'),dashboardList('Project teams',assignmentRows,complete?'No current project assignments.':'Project assignments are unavailable.','/agents','View assignments')));
  body.append(el('p',{class:'small',text:'Available describes local availability, not whether an agent is idle. Assignments are current project records, separate from past participation.'}));
 }else if(area==='Teach Frames'||area==='Creations'){
  let all=store.artifacts.map(a=>({...a,origin:'workspace'})),chatAvailable=true;try{all.push(...(await libraryData()).filter(a=>a.origin==='chat'));}catch(_){chatAvailable=false;body.append(el('div',{class:'banner warning',text:'Chat artifacts could not be read. Counts below cover workspace material only.'}));}
  all=all.filter(a=>area!=='Teach Frames'||a.kind==='teachframe');let source='all';const scoped=el('div');body.append(el('div',{class:'toolbar'},select('Dashboard source',[['all','All sources'],['workspace','Workspace'],['chat','From Chat']],source,v=>{source=v;renderArtifacts();})),scoped);
  function renderArtifacts(){scoped.replaceChildren();if(source==='chat'&&!chatAvailable){scoped.append(empty('Chat material is unavailable','Refresh to try again, or open Chat.',link('Open Chat','/')));return;}
   const rows=all.filter(a=>source==='all'||a.origin===source),current=rows.filter(a=>!a.archived),ready=current.filter(a=>a.stage==='ready'),drafts=current.filter(a=>a.stage!=='ready');
   scoped.append(overview([[current.length,'Current'],[drafts.length,'Drafts'],[ready.length,'Published'],[rows.filter(a=>a.archived).length,'Archived']]));
   const item=a=>el('div',{},button(a.title,()=>readCreation(a).catch(e=>announce(e.message,true)),'dashboard-item'),el('div',{class:'small',text:(kinds[a.kind]||a.kind)+' · '+(a.origin==='chat'?'From Chat':'Workspace')+' · '+date(a.updated_at||a.at)}));
   const sorted=[...current].sort((a,b)=>String(b.updated_at||b.at||'').localeCompare(String(a.updated_at||a.at||'')));
   scoped.append(el('div',{class:'grid'},chart('Sharing status',[['Drafts',drafts.length],['Published',ready.length],['Archived',rows.filter(a=>a.archived).length]],destination),dashboardList('Recently updated',sorted.map(item),'No current material in this source.',destination,'Open '+area),dashboardList('Ready to share',sorted.filter(a=>a.stage==='ready').map(item),'No material is marked ready to share yet.',destination,'Review material'),chart(area==='Teach Frames'?'Sources':'Creation types',area==='Teach Frames'?[['Workspace',current.filter(a=>a.origin==='workspace').length],['From Chat',current.filter(a=>a.origin==='chat').length]]:Object.entries(kinds).map(([k,n])=>[n,current.filter(a=>a.kind===k).length]),destination)));
   scoped.append(el('p',{class:'small',text:'Published marks a creation for everyone signed in to this installation; it is not posted outside it, used for training or written to memory. Chat material belongs to your signed-in account; workspace material is shared here.'}));
  }renderArtifacts();
 }else{
  const catalogue=await projects(),all=catalogue.rows.filter(p=>p.kind!=='library'&&p.kind!=='chat'),sprintResult=area==='Sprints'?await sprintData(catalogue):null;let mission='all';const scoped=el('div');
  body.append(el('div',{class:'toolbar'},select('Dashboard mission',[['all','All missions'],['unassigned','No mission assigned'],...catalogue.missions.map(m=>[m.id,m.name])],mission,v=>{mission=v;renderProjects();})),scoped);
  function renderProjects(){scoped.replaceChildren();const p=all.filter(p=>mission==='all'||(mission==='unassigned'?!p.mission:p.mission?.id===mission)),open=p.filter(p=>!p.done),closed=p.filter(p=>p.done),ids=new Set(p.map(p=>p.id)),grid=el('div',{class:'grid'});
   if(area==='Sprints'){
    const s=sprintResult.rows.filter(s=>ids.has(s.project.id)),active=s.filter(s=>s.active),planned=s.filter(s=>!s.approved&&!s.closed),owners=new Map();for(const sp of active)for(const task of sp.tasks)owners.set(task.who||'Unassigned',(owners.get(task.who||'Unassigned')||0)+1);
    scoped.append(overview([[active.length,'Active sprints'],[planned.length,'Planned sprints'],[active.reduce((n,s)=>n+s.tasks.length,0),'Tasks in active sprints'],[new Set(active.map(s=>s.project.id)).size,'Projects with active work']]));
    const sprintRow=s=>el('div',{},link('Sprint '+s.number+' · '+s.title,projectLink(s.project),''),el('div',{class:'small',text:s.project.name+' · '+count(s.tasks.length,'recorded task')}));
    grid.append(chart('Sprint status',[['Active',active.length],['Planned',planned.length],['In accepted projects',s.filter(s=>s.closed).length]],destination),chart('Recorded task ownership',[...owners.entries()].sort((a,b)=>b[1]-a[1])),dashboardList('Active work',active.map(sprintRow),'No active sprints in this mission view.',destination,'Open Active Sprints'),dashboardList('Planned next',planned.map(sprintRow),'No planned sprints in this mission view.','/roadmaps','Open Roadmaps'));
    if(sprintResult.failures.length)scoped.append(el('div',{class:'banner warning',text:'Incomplete sprint counts. Projects could not be read: '+sprintResult.failures.join(', ')+'. Refresh to try again.'}));
    scoped.append(el('p',{class:'small',text:'Active means approved work in an unaccepted project. Recorded tasks and owners do not establish task completion or workload effort.'}));
   }else if(area==='Roadmaps'){
    const planned=open.filter(p=>p.sprints>0),waiting=open.filter(p=>!p.sprints),done=closed.filter(p=>p.sprints>0);
    scoped.append(overview([[planned.length,'Open roadmaps'],[waiting.length,'Awaiting a roadmap'],[done.length,'Closed roadmaps'],[planned.reduce((n,p)=>n+Number(p.sprints||0),0),'Sprints in open roadmaps']]));
    grid.append(chart('Planning status',[['Open roadmaps',planned.length],['Awaiting a roadmap',waiting.length],['Closed roadmaps',done.length]],destination),dashboardList('Awaiting a roadmap',waiting.map(dashboardProject),'Every open project in this view has a recorded plan.',destination,'Open Roadmaps'),dashboardList('Open roadmaps',planned.map(dashboardProject),'No open roadmaps in this mission view.',destination,'View plans'));
    scoped.append(el('p',{class:'small',text:'A roadmap is a project with recorded sprints. Open and closed follow the project status, matching the Roadmaps page.'}));
   }else{
    scoped.append(overview([[mission==='all'?catalogue.missions.length:mission==='unassigned'?0:1,'Missions in view'],[open.length,'Open projects'],[closed.length,'Completed projects'],[open.filter(p=>!p.mission).length,'Open · no mission']]));
    const missions=catalogue.missions.filter(m=>mission==='all'||m.id===mission);
    grid.append(chart('Projects by mission',[...missions.map(m=>[m.name,p.filter(p=>p.mission?.id===m.id).length]),...(mission==='all'||mission==='unassigned'?[['No mission assigned',p.filter(p=>!p.mission).length]]:[])],destination),chart('Project stages',['Planning','In progress','In review','Closing','Done'].map(stage=>[stage,p.filter(p=>p.stage===stage).length])),dashboardList('Projects in review',open.filter(p=>p.stage==='In review'||p.stage==='Closing').map(dashboardProject),'No projects are currently in review or closing.',destination,'Open Missions'));
   }
   if(area!=='Sprints')grid.append(dashboardList('Recent project activity',[...p].sort((a,b)=>String(b.last_at||'').localeCompare(String(a.last_at||''))).map(dashboardProject),'No projects in this mission view.','/missions','Open Missions'));
   scoped.append(grid);
  }renderProjects();
 }
 stamp.textContent='Loaded '+new Date().toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'})+' · Use Refresh for current records. Filters apply to this dashboard; destination pages open their own views.';
}


export {dashboardPage};
