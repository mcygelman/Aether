/* Own source setup and private reporting UI.
 * Never infer permissions, execute database SQL locally, or manufacture report rows.
 */
import {$,api,button,can,card,el,heading,link,editor,announce,select} from './kit.js';
const API='/api/data-sources';
const encode=encodeURIComponent;
const pretty=value=>JSON.stringify(value,null,2);
async function attempt(run){try{await run();}catch(error){announce(error.message,true);}}
function style(){if(!document.getElementById('source-style'))document.head.append(el('link',{id:'source-style',rel:'stylesheet',href:'/workspace-assets/data-sources.css'}));}
function connectionEditor(source,data){
 const s=source||{transport:'http',roles:['user'],tools:[],timeout:30,enabled:false,allow_model_use:false};
 let roleInputs=[];
 const fields=[
  {name:'name',label:'Source name',value:s.name,required:true,max:100},
  {name:'alias',label:'Chat mention',value:s.alias,required:true,max:60,hint:'For example Personnel. Users can mention @Personnel or ask naturally.'},
  {name:'description',label:'What information does this source provide?',value:s.description,max:500},
  {name:'provider',label:'Database provider (optional description)',value:s.provider,max:80,hint:'For example SQL Server, Oracle, MySQL or PostgreSQL. Database connection settings belong to the MCP service.'},
  {name:'transport',label:'MCP connection',type:'select',value:s.transport,options:[['http','Streamable HTTP'],['stdio','Local service (stdio)']]},
  {name:'url',label:'MCP endpoint',value:s.url,hint:'HTTPS for remote services; HTTP is supported on localhost.'},
  {name:'token',label:s.token_configured?'Service bearer token (leave blank to keep)':'Service bearer token (if required)',type:'password',max:16000},
  {name:'clear_token',label:'Remove saved bearer token',type:'checkbox'},
  {name:'command',label:'Local service command (JSON array)',type:'textarea',value:pretty(s.command||[]),hint:'An already installed service executable and arguments. Keep database credentials in the service configuration, outside this command.'},
  {name:'cwd',label:'Local service working folder (optional)',value:s.cwd},
  {name:'timeout',label:'Request timeout in seconds',type:'number',value:s.timeout||30},
  {name:'guide',label:'Source guide',type:'textarea',value:s.guide,rows:5,max:12000,hint:'Business definitions, useful relationships, reporting examples and known limitations.'},
  {name:'enabled',label:'Enable this source for its roles',type:'checkbox',value:s.enabled},
  {name:'allow_model_use',label:'Allow assigned Chat and agent models to process this source’s metadata and results',type:'checkbox',value:s.allow_model_use,hint:'The selected model may run remotely. Use your organization’s approved model connections.'}
 ];
 editor(source?'Edit source':'Add data source','The DBA configures restricted reporting accounts in the MCP service. Aether connects to that service and controls operator access.',fields,async values=>{
  const command=JSON.parse(values.command||'[]'),transport=values.transport;
  const changed=transport!==s.transport||(transport==='http'?values.url!==s.url:pretty(command)!==pretty(s.command||[])||values.cwd!==(s.cwd||''))||!!values.token||!!values.clear_token;
  await api(API+'/settings',{...s,...values,command,timeout:Number(values.timeout),roles:roleInputs.filter(x=>x.checked).map(x=>x.value),
   enabled:!!values.enabled,allow_model_use:!!values.allow_model_use,clear_token:!!values.clear_token,
   tools:changed?[]:s.tools,revision:data.revision});
 },{wide:true,onReady:form=>{
  const group=el('fieldset',{},el('legend',{text:'Roles that may use this source'}),el('p',{class:'small',text:'These users also need Use Data Sources and Reports. Admins retain access. All users share the DBA account’s database permissions.'}));
  for(const role of data.roles){const input=el('input',{type:'checkbox',value:role.id});input.checked=s.roles.includes(role.id);roleInputs.push(input);group.append(el('label',{class:'source-check'},input,document.createTextNode(role.name)));}
  form.querySelector('#editor-fields').append(group);
 }});
}

function capabilities(source,data){
 const detail=el('details',{},el('summary',{text:'Reporting capabilities ('+source.tools.length+' selected)'}));
 detail.append(el('p',{class:'small',text:'Select only reporting capabilities exposed by your restricted MCP service. Tool annotations describe behavior; database permissions are enforced by the DBA account.'}));
 if(!source.catalog.length){detail.append(el('p',{text:'Save the connection, then Discover tools.'}));return detail;}
 const controls=[];
 for(const tool of source.catalog){
  const prior=source.tools.find(t=>t.name===tool.name),check=el('input',{type:'checkbox'});check.checked=!!prior;
  const kind=el('select',{'aria-label':'Purpose of '+tool.name},el('option',{value:'query',text:'Query / report'}),el('option',{value:'schema',text:'Schema / discovery'}));kind.value=prior?.kind||'query';
  const mapping=el('textarea',{rows:3,'aria-label':'Result field mapping for '+tool.name});mapping.value=pretty(prior?.mapping||{});
  const row=card(tool.name,tool.description||'No description supplied.');
  row.append(el('label',{class:'source-check'},check,document.createTextNode('Expose this tool')),kind,
   el('details',{},el('summary',{text:'Arguments and result mapping'}),el('pre',{text:pretty(tool.inputSchema)}),
    el('p',{class:'small',text:'Optional dotted JSON field paths: rows, columns, total, truncated. Leave {} for rows/columns/total_rows/truncated or a JSON array of objects.'}),mapping));
  controls.push({tool,check,kind,mapping});detail.append(row);
 }
 detail.append(button('Save capabilities',()=>attempt(async()=>{
  const tools=controls.filter(c=>c.check.checked).map(c=>({name:c.tool.name,kind:c.kind.value,mapping:JSON.parse(c.mapping.value||'{}')}));
  await api(API+'/settings',{...source,tools,revision:data.revision});await dataSourcesPage();announce('Source capabilities saved.');
 })));
 return detail;
}

async function watch(job,container){
 let state=job,finished=false;
 const status=el('p',{role:'status',text:'Contacting the data source…'});
 const stop=button('Stop report',()=>attempt(async()=>{await api(API+'/stop',{id:job.id});status.textContent='Stopping this report. The service may finish its database request.';}));
 container.prepend(el('div',{class:'source-job'},status,stop));
 while(state.status==='running'&&container.isConnected){await new Promise(resolve=>setTimeout(resolve,350));state=await api(API+'/jobs?id='+encode(job.id));}
 finished=state.status==='complete';stop.remove();status.textContent=finished?'Report received.':state.error||state.status;
 if(finished&&container.isConnected)await renderRun(await api(API+'/runs?id='+encode(state.run)),container);
}

function runEditor(source,tool,container){
 const defaults=Object.fromEntries(Object.entries(tool.schema?.properties||{}).filter(([,v])=>Object.hasOwn(v,'default')).map(([k,v])=>[k,v.default]));
 editor('Run '+tool.name,'Supply the arguments described by this MCP service. Chat can help construct them.',[
  {name:'arguments',label:'Tool arguments (JSON object)',type:'textarea',value:pretty(defaults),rows:10,required:true,hint:pretty(tool.schema)}
 ],async values=>{const job=await api(API+'/run',{source:source.id,tool:tool.name,arguments:JSON.parse(values.arguments)});setTimeout(()=>attempt(()=>watch(job,$('page-content'))),50);},{wide:true,saveLabel:'Run report'});
}

function tableView(result,layout={}){
 const area=el('section',{class:'source-results'}),filter=el('input',{type:'search',placeholder:'Filter received rows','aria-label':'Filter received rows'}),status=el('p',{role:'status'}),scroll=el('div',{class:'source-table-scroll'}),chart=el('div',{class:'source-bars'});
 const table=result.table;
 let order=table.columns.indexOf(layout.sort_column),ascending=layout.ascending!==false,page=0,numeric=table.columns.indexOf(layout.chart_column);
 filter.value=layout.filter||'';
 area.reportLayout=()=>({filter:filter.value,sort_column:table.columns[order]||'',ascending,chart_column:table.columns[numeric]||''});
 const choice=select('Chart numeric column', [['-1','No chart'],...table.columns.map((n,i)=>[String(i),n])],String(numeric),v=>{numeric=Number(v);render();});
 function render(){
  const query=filter.value.toLowerCase();let rows=table.rows.filter(row=>JSON.stringify(row).toLowerCase().includes(query));
  if(order>=0)rows=[...rows].sort((a,b)=>{const x=a[order],y=b[order],v=typeof x==='number'&&typeof y==='number'?x-y:String(x??'').localeCompare(String(y??''));return ascending?v:-v;});
  const pages=Math.max(1,Math.ceil(rows.length/100));page=Math.min(page,pages-1);
  status.textContent=rows.length+' matching received rows · Page '+(page+1)+' of '+pages+'. Filtering affects this view; exports contain all received rows.';
  const view=el('table'),head=el('tr');table.columns.forEach((name,i)=>head.append(el('th',{scope:'col'},button(name+(order===i?(ascending?' ↑':' ↓'):''),()=>{ascending=order===i?!ascending:true;order=i;render();}))));
  view.append(el('thead',{},head));const body=el('tbody');
  for(const row of rows.slice(page*100,page*100+100)){const tr=el('tr');row.forEach(value=>tr.append(el('td',{text:value===null?'':typeof value==='object'?JSON.stringify(value):String(value)})));body.append(tr);}
  view.append(body);scroll.replaceChildren(view);chart.replaceChildren();
  if(numeric>=0){const chartRows=rows.filter(r=>typeof r[numeric]==='number'&&Number.isFinite(r[numeric])).slice(0,20),min=Math.min(0,...chartRows.map(r=>r[numeric])),max=Math.max(1,...chartRows.map(r=>r[numeric]));
   chart.append(el('p',{class:'small',text:'First '+chartRows.length+' numeric rows in this view; no grouping or aggregation.'}));
   for(const row of chartRows)chart.append(el('label',{},el('span',{text:String(row[0]??'')}),el('meter',{min,max,value:row[numeric],'aria-label':String(row[0]??'')}),el('span',{text:String(row[numeric])})));
  }
 }
 filter.oninput=()=>{page=0;render();};area.append(filter,status,scroll,el('div',{class:'actions'},button('Previous page',()=>{page=Math.max(0,page-1);render();}),button('Next page',()=>{page++;render();})),choice,chart);render();return area;
}

async function renderRun(run,container){
 const panel=card(run.source_name+' · '+run.tool);panel.classList.add('source-run');
 panel.append(el('p',{text:run.at+' · '+run.result.completeness+' result · '+(run.result.returned_rows??'Unknown number of')+' received rows'}),el('p',{class:'small',text:run.result.explanation}),
  el('details',{},el('summary',{text:'Source and report parameters'}),el('pre',{text:pretty({source:run.source_id,source_version:run.source_version,tool:run.tool,arguments:run.arguments,origin:run.origin})})));
 const table=run.result.table?tableView(run.result,run.layout||{}):null;
 if(table)panel.append(table);else panel.append(el('pre',{text:run.result.text||'No textual result.'}));
 const actions=el('div',{class:'actions'},link('Export result JSON',API+'/export?id='+encode(run.id)+'&format=json'));
 if(run.result.table)actions.append(link('Export received rows CSV',API+'/export?id='+encode(run.id)+'&format=csv'));
 actions.append(button('Save report definition',()=>editor('Save report','Save this tool call and its parameters for another run. This dated result remains a separate snapshot.',[
  {name:'title',label:'Report name',required:true,max:150,value:run.source_name+' report'},
  {name:'description',label:'Meaning, assumptions and intended use',type:'textarea',max:4000}
 ],v=>api(API+'/save-report',{run:run.id,layout:table?.reportLayout()||{},...v}))));panel.append(actions);container.prepend(panel);
}

export async function dataSourcesPage(){
 style();const manage=can('manage_data_sources')&&new URLSearchParams(location.search).get('manage')==='1';
 heading(manage?'Data source settings':'Data Sources & Reports','Ask questions, inspect real results, and save reports backed by your organization’s MCP services.',[
  link('Open Chat','/'),...(can('manage_data_sources')?[link(manage?'Use sources':'Manage sources','/workspace?view=data-sources'+(manage?'':'&manage=1'))]:[])
 ]);
 const root=$('page-content');root.classList.add('data-sources');
 if(!manage&&!can('use_data_sources')){root.append(card('Source access','Ask an Admin for Use Data Sources and Reports and the role assigned to the source.'));return;}
 const data=await api(API+(manage?'?manage=1':''));
 if(manage){
  root.append(button('Add data source',()=>connectionEditor(null,data),'primary'));
  root.append(el('p',{class:'banner',text:'Database accounts and drivers live in your MCP service. Save a connection, discover its tools, select reporting capabilities, then enable access. No service is installed automatically.'}));
  for(const s of data.sources){const box=card(s.name,s.description);box.append(el('p',{text:'@'+s.alias+' · '+(s.enabled?'Enabled':'Disabled')+' · '+s.transport}),el('div',{class:'actions'},
   button('Edit connection',()=>connectionEditor(s,data)),button('Discover tools',()=>attempt(async()=>{announce('Discovering MCP capabilities…');await api(API+'/discover',{source:s.id});await dataSourcesPage();announce('Capabilities discovered. Select the reporting tools to expose.');})),
   button('Delete connection',()=>{if(confirm('Remove '+s.name+'? Saved report records are retained but will be unavailable.'))attempt(async()=>{await api(API+'/settings',{id:s.id,action:'delete',revision:data.revision});await dataSourcesPage();});})),capabilities(s,data));root.append(box);}
 }else{
  if(!data.sources.length)root.append(card('No available sources','An Admin can configure an MCP service and grant your role access.'));
  for(const s of data.sources){const box=card(s.name,s.description);box.append(el('p',{text:'@'+s.alias+' · '+(s.provider||'MCP data source')}),link('Ask about this source','/?source='+encode(s.id)));
   if(s.guide)box.append(el('details',{},el('summary',{text:'Source guide'}),el('p',{class:'source-guide',text:s.guide})));
   const advanced=el('details',{},el('summary',{text:'Run a tool directly'}));for(const tool of s.tools)advanced.append(button(tool.name,()=>runEditor(s,tool,root)));box.append(advanced);root.append(box);
  }
  const reports=card('Saved reports','These report definitions and result snapshots are private to your account. Each run checks your current source access.');
  for(const report of data.reports){reports.append(el('div',{class:'source-report'},el('h3',{text:report.title}),el('p',{text:report.description}),button('Run with parameters',()=>editor(report.title,'Rerun the saved definition with these tool arguments. SQL parameters are interpreted by the service.',[
   {name:'arguments',label:'Tool arguments (JSON object)',type:'textarea',rows:8,value:pretty(report.arguments),required:true}
  ],async values=>{const job=await api(API+'/run-report',{id:report.id,arguments:JSON.parse(values.arguments)});setTimeout(()=>attempt(()=>watch(job,$('page-content'))),50);},{wide:true,saveLabel:'Run report'})),
   button('View saved snapshot',()=>attempt(async()=>renderRun(await api(API+'/runs?id='+encode(report.snapshot_id)),root))),
   button('Remove definition',()=>attempt(async()=>{await api(API+'/remove-report',{id:report.id});await dataSourcesPage();}))));}
  if(!data.reports.length)reports.append(el('p',{text:'Save a useful result as a report definition here or ask Chat to save it.'}));root.append(reports);
  const runs=await api(API+'/runs');const recent=el('details',{},el('summary',{text:'Recent report runs'}));for(const run of runs)recent.append(button(run.at+' · '+run.source_name+' · '+run.tool,()=>attempt(async()=>renderRun(await api(API+'/runs?id='+encode(run.id)),root))));root.append(recent);
  const runId=new URLSearchParams(location.search).get('run');if(runId)await renderRun(await api(API+'/runs?id='+encode(runId)),root);
 }
}
