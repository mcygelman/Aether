/* Owner: workspace project catalogue, artifact project links and sprint projections.
 * Must never: render an area, change project records, or infer task completion.
 */
import {announce, api, el, link, params, projectLink} from './kit.js';
let artifactProjects=null;
function resetProjectCache(){artifactProjects=null;}
// Roadmap rows (kind 'roadmap', project folders) are drawn by Roadmaps and Missions; these readers want commissions.
const commission=p=>p.kind!=='roadmap';
async function projects(){const data=await api('/api/campaigns');const missions=(data.campaigns||[]).map(m=>({...m,projects:m.projects.filter(commission)}));return {missions,rows:[...(data.unfiled||[]).filter(commission).map(p=>({...p,mission:null})),...missions.flatMap(m=>m.projects.map(p=>({...p,mission:{id:m.id,name:m.name}})))]};}
async function creationProjects(){
 if(!artifactProjects)artifactProjects=projects().then(v=>v.rows).catch(()=>{announce('Project names are unavailable. Existing links are kept; refresh to try again.',true);return [];});
 return artifactProjects;
}
function projectOptions(rows){return rows.map(p=>[p.id,p.name+(p.mission?' · '+p.mission.name:'')]);}
async function projectField(a,copy){
 const rows=await creationProjects(),existing=a?.project_id||'',requested=existing||params.get('project')||'';
 const value=rows.some(p=>p.id===requested)?requested:(!copy&&existing?existing:'');
 const options=[['','No project'],...projectOptions(rows)];
 if(value&&!rows.some(p=>p.id===value))options.push([value,'Unavailable project · keep existing link']);
 return {name:'project_id',label:'Project (optional)',type:'select',options,value,hint:'Workspace creations are visible to signed-in operators here. Linking adds this artifact to the project’s Creations panel; it does not send it to agents.'};
}
async function projectContext(a){
 if(!a.project_id)return null;
 const p=(await creationProjects()).find(p=>p.id===a.project_id);
 return p?link('Project: '+p.name,projectLink(p),''):el('span',{class:'muted',text:'Linked project unavailable here'});
}
async function sprintData(catalogue){const rows=[];const failures=[];let next=0;const projectsToRead=catalogue.rows.filter(p=>p.sprints>0&&p.kind!=='library'&&p.kind!=='chat');async function worker(){while(next<projectsToRead.length){const p=projectsToRead[next++];try{const v=await api('/api/atelier/read?commission='+encodeURIComponent(p.id)),approved=new Set(v.facts.approvals.map(a=>a.sprint));for(const s of v.facts.plan.sprints)rows.push({...s,project:p,approved:approved.has(s.number),active:approved.has(s.number)&&!v.facts.acceptance,closed:!!v.facts.acceptance});}catch(e){failures.push(p.name);}}}await Promise.all(Array.from({length:Math.min(3,projectsToRead.length)},worker));return {rows,failures};}

export {creationProjects, projectContext, projectField, projectOptions, projects, resetProjectCache, sprintData};
