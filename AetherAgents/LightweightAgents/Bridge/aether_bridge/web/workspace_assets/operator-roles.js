/* Own named-role editing in Human Staff. Never authorize changes or store passwords. */
import {api,button,can,card,editor,el} from './kit.js';
export function roleCards(directory){
 const section=card('Custom roles','Users can hold several roles. Their permissions combine with direct account permissions. Admin always has all permissions.');
 const edit=role=>editor(role?'Edit '+role.name:'Create role','Choose the permissions this role grants.',[
  {name:'name',label:'Role name',required:true,max:80,value:role?.name||''},
  ...Object.entries(directory.permissions).map(([name,label])=>({name,label,type:'checkbox',value:role?.permissions.includes(name)||false}))
 ],v=>api('/api/access/roles',{action:'save',revision:directory.revision,id:role?.id,name:v.name,permissions:Object.keys(directory.permissions).filter(p=>v[p])}));
 if(can('manage_roles'))section.append(button('Create role',()=>edit(null),'primary','manage_roles'));
 for(const role of directory.roles){const row=card(role.name,role.permissions.map(p=>directory.permissions[p]).join(' · ')||'No additional permissions');
  const count=directory.accounts.filter(u=>u.roles.includes(role.id)).length;
  row.append(el('p',{class:'small',text:count+' assigned accounts'}));
  if(can('manage_roles'))row.append(button('Edit role',()=>edit(role),'','manage_roles'),button('Delete role',()=>editor('Delete '+role.name,'Assigned roles must be removed from accounts before deletion.',[{name:'confirmation',label:'Type the role name',required:true}],async v=>{if(v.confirmation!==role.name)throw Error('Type the exact role name.');await api('/api/access/roles',{action:'delete',id:role.id,revision:directory.revision});},{saveLabel:'Delete role'}),'','manage_roles'));
  section.append(row);
 }
 return section;
}
