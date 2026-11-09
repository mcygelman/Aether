/* Owner: Staff → Create a specialist (Michel, 2026-11-05): describe a lightweight specialist in plain words, preview
 * it, and add it to the AI staff that sprints hire from.
 * Must never: send paths or identity text from the page, grant tools or permissions, copy memories, or replace an agent.
 */
import {$,api,button,can,el,heading,link,announce} from './kit.js';

// Starting points only: every field stays the person's to change.
const EXAMPLES={
 blank:{name:'',role:'',remit:'',inputs:'',steps:'',limits:'',reviews:false},
 writer:{name:'Marlowe',role:'Report Writer',remit:'Turn a sprint’s findings into a clear, short report for people who were not there.',
  inputs:'the findings or files to report on, who will read the report and how long it should be',
  steps:'List the points the readers need, most important first\nDraft the report in plain words, one idea per paragraph\nCheck every figure and claim against the given files\nNote anything the files did not support',
  limits:'Never add a fact the files do not support\nDo not change or delete the source files',reviews:false},
 reviewer:{name:'Vera',role:'Code Reviewer',remit:'Review a code change for correctness, clarity and missing tests, and report findings by severity.',
  inputs:'the change or files to review, what the change is meant to do and how to run its tests',
  steps:'Read what the change is meant to do before reading the code\nTrace each changed function to its callers and its tests\nRun the tests you are allowed to run and record the results\nList findings from most to least severe, each with the line it concerns',
  limits:'Never rewrite the code under review; report findings instead\nDo not approve work you wrote yourself',reviews:true},
 analyst:{name:'Quinn',role:'Data Analyst',remit:'Answer a question from a granted data file with a reproducible calculation and a short explanation.',
  inputs:'the question, the data file and what each column means',
  steps:'Restate the question and the columns it needs\nCheck the data for missing or odd values and say how you treat them\nCompute the answer with a method someone else can rerun\nExplain the result and its limits in a few sentences',
  limits:'Never guess a value that is missing from the data\nDo not send the data outside the sprint’s folders',reviews:false},
};

function field(label,hint,input){const id='sc-'+input.name;input.id=id;return el('div',{class:'sc-field'},el('label',{for:id,text:label}),hint?el('p',{class:'small',text:hint}):null,input);}
const lines=text=>text.split('\n').map(s=>s.trim()).filter(Boolean);

export async function createSpecialistPage(){
 if(!document.getElementById('specialist-create-style'))document.head.append(el('link',{id:'specialist-create-style',rel:'stylesheet',href:'/workspace-assets/specialist-create.css'}));
 heading('Create a specialist','Describe a new AI specialist in plain words. It joins your AI staff with an empty memory, and a lead can hire it in any sprint.',[link('AI Staff','/agents')]);
 const allowed=can('manage_missions'),root=$('page-content');
 const inputs={name:el('input',{name:'name',autocomplete:'off',maxlength:'32',placeholder:'e.g. Marlowe'}),
  role:el('input',{name:'role',autocomplete:'off',maxlength:'80',placeholder:'e.g. Report Writer'}),
  remit:el('textarea',{name:'remit',rows:'2',maxlength:'500',placeholder:'e.g. Turns a sprint’s findings into a short report for people who were not there.'}),
  inputs:el('input',{name:'inputs',autocomplete:'off',maxlength:'300',placeholder:'e.g. the findings, who will read the report, how long it should be'}),
  steps:el('textarea',{name:'steps',rows:'5',placeholder:'e.g.\nList the points the readers need\nDraft the report in plain words'}),
  limits:el('textarea',{name:'limits',rows:'3',placeholder:'e.g. Never add a fact the files do not support'})};
 const makes=el('input',{type:'radio',name:'sc-kind',id:'sc-makes',value:'makes'}),reviews=el('input',{type:'radio',name:'sc-kind',id:'sc-reviews',value:'reviews'});
 const example=el('select',{id:'sc-example'},...[['blank','Start blank'],['writer','Report writer'],['reviewer','Code reviewer'],['analyst','Data analyst']].map(([v,t])=>el('option',{value:v,text:t})));
 const error=el('p',{class:'form-error',role:'alert'}),preview=el('div',{class:'sc-preview','aria-live':'polite'});
 const add=button('Add to my AI staff',()=>save(),'primary'),done=el('div');
 const body=()=>({name:inputs.name.value.trim(),role:inputs.role.value,remit:inputs.remit.value,inputs:inputs.inputs.value,
  steps:lines(inputs.steps.value),limits:lines(inputs.limits.value),reviews:reviews.checked});
 function fill(name){const e=EXAMPLES[name];for(const k of Object.keys(inputs))inputs[k].value=e[k];makes.checked=!e.reviews;reviews.checked=e.reviews;done.replaceChildren();refresh();}
 let timer=0,request=0;
 function refresh(){clearTimeout(timer);timer=setTimeout(show,450);}
 async function show(){
  const mine=++request,b=body();
  if(!b.name&&!b.role&&!b.remit){preview.replaceChildren(el('p',{class:'muted',text:'Fill in the form, or start from an example, to see the specialist here.'}));return;}
  try{const v=await api('/api/tasker/specialist-preview',b);if(mine!==request)return;error.textContent='';draw(v,b);}
  catch(e){if(mine!==request)return;preview.replaceChildren(el('p',{class:'muted',text:'Not ready yet: '+e.message+'.'}));}
 }
 function draw(v,b){
  const list=(items)=>el('ol',{},...items.map(t=>el('li',{text:t})));
  preview.replaceChildren(
   el('div',{class:'eyebrow',text:'Your specialist · lightweight · empty memory'}),
   el('h2',{text:v.name+' · '+v.role}),el('p',{text:b.remit}),
   el('h3',{text:'Before it starts, it checks'}),el('p',{text:b.inputs}),
   el('h3',{text:'How it works'}),list(b.steps),
   el('h3',{text:'What it never does'}),el('ul',{},...b.limits.map(t=>el('li',{text:t}))),
   el('h3',{text:'How it hands back its work'}),el('p',{text:b.reviews?'A verdict: PASS, or FAIL with the criteria that failed, or UNVERIFIED with the evidence it lacked.':'Its deliverables, the result it verified, and what remains uncertain.'}),
   el('p',{class:'small',text:'It also follows the rules every staff member shares: it works only on its assigned task, writes only the files asked for, talks only to the lead, and records one lesson at the end of each sprint.'}),
   el('details',{},el('summary',{text:'Show the identity file it will get'}),el('pre',{class:'sc-identity',text:v.text})));
 }
 async function save(){
  if(!allowed)return;error.textContent='';add.disabled=true;
  try{const v=await api('/api/tasker/specialist',body());const c=v.created;
   announce(c.name+' joined your AI staff.');
   done.replaceChildren(el('section',{class:'banner'},el('strong',{text:c.name+' joined your AI staff. '}),
    document.createTextNode('A lead can hire '+c.name+' in the next sprint. Its memory starts empty and grows from its own work. '),
    link('Open AI Staff','/agents','link'),document.createTextNode(' · '),button('Create another',()=>fill('blank'),'link')));
   done.scrollIntoView({block:'nearest'});
  }catch(e){error.textContent=e.message;}
  finally{add.disabled=!allowed;}
 }
 for(const i of Object.values(inputs))i.addEventListener('input',refresh);
 for(const r of [makes,reviews])r.addEventListener('change',refresh);
 example.addEventListener('change',()=>fill(example.value));
 makes.checked=true;
 const form=el('section',{class:'ui-card sc-form'},
  el('div',{class:'sc-field'},el('label',{for:'sc-example',text:'Start from an example (optional)'}),example),
  field('Name','One word, like Marlowe or ReportWriter. Leads hire it by this name.',inputs.name),
  field('Job title','',inputs.role),
  field('What it does','',inputs.remit),
  field('What it needs from a task','What it checks for before it begins.',inputs.inputs),
  field('How it works','One step per line, in order. Two to ten steps.',inputs.steps),
  field('What it must never do','One rule per line.',inputs.limits),
  el('fieldset',{class:'sc-kind'},el('legend',{text:'What kind of work it does'}),
   el('label',{for:'sc-makes'},makes,document.createTextNode(' It makes things: reports, code, analyses')),
   el('label',{for:'sc-reviews'},reviews,document.createTextNode(' It reviews others’ work and gives a verdict'))),
  error,el('div',{class:'actions'},add),done);
 if(!allowed){add.disabled=true;form.prepend(el('p',{class:'banner warning',text:'Your operator role cannot add staff. You can still describe and preview a specialist.'}));}
 root.append(el('div',{class:'sc-layout'},form,el('section',{class:'ui-card sc-side'},preview)),
  el('details',{class:'ui-card sc-advanced'},el('summary',{text:'Advanced: the full Agent Editor'}),
   el('p',{class:'muted',text:'Template authoring for the older specialist catalog, draft import and export, and planned heavyweight features. Most people never need it, and what it makes does not appear in your AI staff.'}),
   link('Open the full Agent Editor','/agent-editor/','link')));
 show();
}
