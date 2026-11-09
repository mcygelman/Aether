/* Owner: human operator directory and role/account management views.
 * Must never: own model connections, store passwords, or replace server permission checks.
 */
import {$, api, button, can, card, editor, el, heading, link, operator} from './kit.js';
import {mountAssignments} from './key-assignments.js';
import {roleCards} from './operator-roles.js';
function adminPermissionDisplay(form){
 const role=form.elements.namedItem('role');
 const boxes=[...form.querySelectorAll('input[type="checkbox"]')];
 let userValues=boxes.map(box=>box.checked);
 function sync(){const admin=role.value==='admin';boxes.forEach((box,i)=>{box.checked=admin||userValues[i];box.disabled=admin;});}
 role.addEventListener('change',()=>{if(role.value==='admin')userValues=boxes.map(box=>box.checked);sync();});
 sync();
}
async function operatorDirectory(roles=false){
 const directory=await api('/api/access/accounts');
 const permissionFields=()=>Object.entries(directory.permissions).map(([name,label])=>({name,label,type:'checkbox',value:false}));
 const roleFields=(user={})=>directory.roles.map(r=>({name:'role_'+r.id,label:'Role: '+r.name,type:'checkbox',value:(user.roles||[]).includes(r.id)}));
 const chosenRoles=v=>directory.roles.filter(r=>v['role_'+r.id]).map(r=>r.id);
 const passwordFields=label=>[{name:'password',label,type:'password',required:true,max:256,hint:'Use 12–256 characters. The operator can change this password after signing in.'},{name:'confirmation',label:'Confirm password',type:'password',required:true,max:256}];
 const addAccount=()=>editor('Add operator account','Create an active account without signing out. Admin includes all permissions; additional management permissions apply to User accounts.',[
  {name:'name',label:'Display name',required:true,max:120},{name:'username',label:'Username',required:true,max:64,hint:'3–64 letters, numbers, dots, hyphens or underscores.'},...passwordFields('Initial account password'),
  {name:'role',label:'Base role',type:'select',options:[['user','User'],['admin','Admin']],value:'user'},...roleFields(),...permissionFields()
 ],async v=>{await api('/api/access/accounts',{action:'create',revision:directory.revision,name:v.name,username:v.username,password:v.password,confirmation:v.confirmation,role:v.role,roles:chosenRoles(v),permissions:Object.keys(directory.permissions).filter(p=>v[p])});},{saveLabel:'Create account',onReady:adminPermissionDisplay});
 const setPassword=user=>editor('Set password for '+user.name,'Set a new account password for '+user.username+'. All of this account’s current sessions will end. Its access, Chat history and API vault are unchanged.',passwordFields('New account password'),async v=>{await api('/api/access/accounts',{action:'set_password',revision:directory.revision,id:user.id,password:v.password,confirmation:v.confirmation});},{saveLabel:'Set password and sign out account'});
 const deleteAccount=user=>editor('Delete account for '+user.name,'Permanently remove sign-in access for '+user.username+' and end its sessions. Chat history and shared records are retained; this does not erase their data. A new account with the same username will not inherit its private chats. Disable the account with Edit access if you may need to restore access later.',[
  {name:'confirmation',label:'Type '+user.username+' to confirm',required:true,max:64}
 ],async v=>{await api('/api/access/accounts',{action:'delete',revision:directory.revision,id:user.id,confirmation:v.confirmation});},{saveLabel:'Delete account'});
 heading(roles?'Operator Roles':'Human Operators',roles?'Admin has full access. User has full Chat and view-only access elsewhere, with optional management permissions.':'The human accounts that can sign in to this installation.',[...(can('manage_accounts')?[button('Add account',addAccount,'primary','manage_accounts')]:[]),link('Your account','/login')]);
 const root=$('page-content');
 if(roles){root.append(roleCards(directory));const definitions=el('div',{class:'grid'},card('Admin','Can do anything, including creating other Admins and assigning operator permissions.'),card('User','Full Chat: conversations, transforms, history and exports. All other areas are view-only unless a management permission is added.'));for(const [id,label]of Object.entries(directory.permissions))definitions.append(card(label,({manage_missions:'Create and manage missions, projects, roadmaps, sprints and project teams.',manage_agents:'Add agents, manage their folder locations and change availability.',manage_teach:'Create, edit and archive shared Teach Frames. Other Creations remain view-only.',manage_guides:'Create, import, edit and archive the operator Guides collection.',manage_dashboards:'Edit dashboard titles and explanatory notes.',manage_settings:'Manage model connections, network settings and Emergency Halt.',manage_accounts:'Create accounts and manage access within your own permission level.',manage_roles:'Define named roles within your own permission level.',manage_api_keys:'Manage key secrets after unlocking API management.',manage_key_assignments:'Assign saved keys and suspend API use after unlocking API management.'})[id]));root.append(definitions);}
 root.append(el('h2',{text:'Operator accounts'}));const grid=el('div',{class:'grid'});root.append(grid);
 for(const user of directory.accounts){const c=card(user.name,user.username);c.append(el('span',{class:'pill',text:user.role==='admin'?'Admin':'User'}),el('span',{class:'pill',text:user.active?'Active':'Disabled'}));if(user.role==='user')c.append(el('p',{class:'muted',text:user.permissions.map(p=>directory.permissions[p]).join(' · ')||'Full Chat · View only elsewhere'}));if(can('manage_accounts'))c.append(el('div',{class:'actions'},button('Edit access',()=>{
  editor('Access for '+user.name,'Named roles and direct permissions combine. Admin includes every permission. Managers cannot grant or manage access above their own.',[{name:'role',label:'Base role',type:'select',options:[['user','User'],['admin','Admin']],value:user.role},{name:'active',label:'Account status',type:'select',options:[['yes','Active'],['no','Disabled']],value:user.active?'yes':'no'},...roleFields(user),...Object.entries(directory.permissions).map(([id,label])=>({name:id,label,type:'checkbox',value:user.direct_permissions.includes(id)}))],async v=>{await api('/api/access/accounts',{revision:directory.revision,id:user.id,role:v.role,active:v.active==='yes',roles:chosenRoles(v),permissions:Object.keys(directory.permissions).filter(p=>v[p])});if(user.id===operator.id)location.reload();},{onReady:adminPermissionDisplay});
 },'','manage_accounts'),...(user.id!==operator.id?[button('Set new password',()=>setPassword(user),'','manage_accounts'),button('Delete account',()=>deleteAccount(user),'','manage_accounts')]:[link('Change your password','/login?next='+encodeURIComponent(location.pathname+location.search))])));grid.append(c);}
 if(!roles){const keys=el('section',{class:'ui-card'});root.append(keys);await mountAssignments(keys);}
 root.append(el('p',{class:'small',text:'The first account is an Admin. Self-registered accounts start as Users; Admins can add accounts here with their chosen access. At least one active Admin must remain. Admins can reset another account’s password or delete its sign-in access. Deletion retains Chat history and shared records; disabling an account allows access to be restored. Another Admin must delete your own account.'}));
}
async function humansPage(){await operatorDirectory(false);}
async function rolesPage(){await operatorDirectory(true);}

export {humansPage, rolesPage};
