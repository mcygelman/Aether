/* Owner: shipped specialist browsing and explicit fresh-agent installation.
 * Must never: import resident history, author arbitrary server paths, start inference or bypass server permissions.
 */
import {$,api,button,can,card,download,editor,el,heading,link,search,select,announce} from './kit.js';

export async function catalogPage(){
 if(!document.getElementById('specialist-catalog-style'))document.head.append(el('link',{id:'specialist-catalog-style',rel:'stylesheet',href:'/workspace-assets/specialist-catalog.css'}));
 const data=await api('/api/agent-catalog');
 heading('Specialist catalog','Choose reusable lightweight roles for your team. Each added agent gets a fresh identity and cartridge.',[link('AI Staff','/agents'),link('Custom templates','/agent-editor/#custom-templates'),link('Technical Learning','/workspace?view=technical-learning')]);
 const root=$('page-content'),selected=new Set();let query='',category='all';
 root.append(el('p',{class:'banner',text:'Adding specialists starts no model work and copies no existing agent memory. Assign them to a project team after adding them. Heavyweight choices remain separate.'}));
 const summary=el('p',{class:'small'}),grid=el('div',{class:'grid'}),results=el('p',{role:'status'});
 const add=button('Add selected specialists',()=>{
   const chosen=data.templates.filter(t=>selected.has(t.id));if(!chosen.length){announce('Select at least one specialist.',true);return;}
   const requestId=crypto.randomUUID();
   editor('Add '+chosen.length+' specialist'+(chosen.length===1?'':'s'),
    chosen.map(t=>t.name).join('; ')+'. New homes will be created under '+data.default_folder+'. Existing agents are preserved. Repeating this form submission retries the same request.',
    [{name:'deployment',label:'Default model connection',type:'select',value:'unconfigured',options:[['unconfigured','Configure later'],...data.connections.map(n=>[n,n])],hint:'Choose each agent’s final model in its project team. Recommended model limits are guidance, not enforced budgets.'}],
    async values=>{const receipt=await api('/api/agent-catalog/install',{request_id:requestId,revision:data.revision,templates:chosen.map(t=>t.id),deployment:values.deployment});if(!receipt.complete)throw Error('Installation is incomplete. Retain this request and retry.');},
    {saveLabel:'Create fresh agents'});
 },'primary');
 add.disabled=!can('manage_agents')||!data.default_folder;
 const update=()=>{summary.textContent=selected.size+' selected · '+data.templates.length+' available · Every role is lightweight';};
 function render(){
  grid.replaceChildren();const visible=data.templates.filter(t=>(category==='all'||t.category===category)&&(!query||[t.name,t.remit,t.category].join(' ').toLowerCase().includes(query)));
  results.textContent=visible.length+' matching specialists';
  for(const t of visible){
   const c=card(t.name,t.remit),check=el('input',{type:'checkbox','aria-label':'Select '+t.name});check.checked=selected.has(t.id);check.disabled=!can('manage_agents')||!data.default_folder;
   check.onchange=()=>{if(check.checked)selected.add(t.id);else selected.delete(t.id);update();};
   c.prepend(el('div',{class:'eyebrow',text:t.category}));c.append(el('label',{class:'specialist-select'},check,document.createTextNode('Select this specialist')));
   const detail=el('details',{},el('summary',{text:'Role contract and qualification'}));
   detail.append(el('p',{text:'Inputs: '+t.inputs}),el('ol',{},...t.workflow.map(s=>el('li',{text:s}))),el('p',{text:'Writable project folders: '+t.deliverables.join(', ')}),el('p',{text:'Allowed tools: '+t.allowed_tools.join(', ')}),el('p',{text:t.boundaries.join(' ')}),el('p',{text:t.origin}),el('p',{text:'Suggested context: '+t.recommendations.context_window+' tokens; output: '+t.recommendations.max_output_tokens+' tokens; turn budget: '+t.recommendations.turn_model_calls+' model calls. Configure these in the model/project settings.'}),el('p',{text:'Qualification scenario: '+t.qualification.scenario}),el('p',{text:'Expected evidence: '+t.qualification.acceptance}),el('p',{class:'small',text:t.qualification.status}));
   c.append(detail,button('Export role template',()=>download(t,t.id+'-template')));grid.append(c);
  }
 }
 root.append(el('div',{class:'toolbar'},search('Search specialist roles',v=>{query=v;render();}),select('Category',[['all','All categories'],...[...new Set(data.templates.map(t=>t.category))].map(v=>[v,v])],category,v=>{category=v;render();}),add),summary,results,grid);
 if(!can('manage_agents'))root.prepend(el('p',{class:'banner',text:'View and export the catalog. Adding agents requires Manage Agents permission.'}));
 if(!data.default_folder)root.prepend(el('p',{class:'banner'},document.createTextNode('Choose an agents folder before adding specialists. '),link('Open AI Staff','/agents')));
 update();render();
}
