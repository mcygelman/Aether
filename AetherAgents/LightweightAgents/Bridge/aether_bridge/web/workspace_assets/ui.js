/* Owner: workspace bootstrap and lazy view routing only.
 * Must never: contain feature UI, domain data, editor logic or new cross-area helpers.
 */
import {$, view, store, heading, link, el, workspaceLink, configureWorkspace, refreshButton, load} from './kit.js';
import {resetProjectCache} from './project-data.js';
const routes={
 'chat-home':async()=> (await import('./chat-dashboard.js')).chatDashboard(),
 sprints:async()=> (await import('./sprints.js')).sprintsPage(),
 teach:async()=> (await import('./teach.js')).teachPage(),
 creations:async()=> (await import('./creations.js')).libraryPage(),
 humans:async()=> (await import('./staff.js')).humansPage(),
 'technical-learning':async()=> (await import('./technical-learning.js')).learningPage(),
 'agent-catalog':async()=> (await import('./specialist-catalog.js')).catalogPage(),
 'create-specialist':async()=> (await import('./specialist-create.js')).createSpecialistPage(),
 roles:async()=> (await import('./staff.js')).rolesPage(),
 settings:async()=> (await import('./settings.js')).settingsPage(),
 'data-sources':async()=> (await import('./data-sources.js')).dataSourcesPage(),
 storage:async()=> (await import('./settings.js')).storagePage(),
 recovery:async()=> (await import('./recovery.js')).recoveryPage(),
 security:async()=> (await import('./settings.js')).securityPage(),
 halt:async()=> (await import('./settings.js')).haltPage(),
 dashboard:async()=> (await import('./dashboards.js')).dashboardPage(),
 help:async()=> (await import('./help.js')).helpPage(),
 welcome:async()=> (await import('./welcome.js')).welcomePage()
};
async function renderView(){
 if(Object.hasOwn(routes,view))await routes[view]();
 else if(view==='roadmaps')location.replace('/roadmaps');
 else{heading('Workspace','Choose an area from the navigation.');$('page-content').append(link('Open Chat','/'));}
 if(store.halted&&view!=='halt')$('page-content').prepend(el('div',{class:'banner warning'},document.createTextNode('New work is stopped. '),link('Manage stopped work',workspaceLink('halt'),'')));
}
configureWorkspace({render:renderView,beforeLoad:resetProjectCache});
refreshButton.onclick=load;
load();
