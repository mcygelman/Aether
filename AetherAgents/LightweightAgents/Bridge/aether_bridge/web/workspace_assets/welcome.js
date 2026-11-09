/* Owner: the one-time welcome of a new install's default Admin (Michel, 2026-11-07): their own password first, then
 * their API key (DeepSeek, Claude or OpenAI), finished with one button. The server does the work and decides whether
 * the welcome is due.
 * Must never: keep or log a password or key, send anything but this form, or show again once finished or skipped.
 */
import {$,api,button,el,heading} from './kit.js';

// The hosted providers the welcome offers; the server's list (aether/atelier/welcome.py) decides what is saved.
const PROVIDERS={
 deepseek:{label:'DeepSeek',where:'your DeepSeek account',placeholder:'sk-…'},
 anthropic:{label:'Claude (Anthropic)',where:'the Claude Console (platform.claude.com)',placeholder:'sk-ant-…'},
 openai:{label:'OpenAI',where:'your OpenAI account (platform.openai.com)',placeholder:'sk-…'},
};

function field(label,hint,input){return el('div',{class:'wl-field'},el('label',{for:input.id,text:label}),hint?el('p',{class:'small',text:hint}):null,input);}

export async function welcomePage(){
 if(!document.getElementById('welcome-style'))document.head.append(el('link',{id:'welcome-style',rel:'stylesheet',href:'/workspace-assets/welcome.css'}));
 const session=await api('/api/access/session');
 heading('Welcome to Aether',session.welcome?'Two things and you are ready: your own password, and your API key.':'');
 const root=$('page-content');
 if(!session.welcome){root.append(el('section',{class:'ui-card'},el('p',{text:'Your welcome is already done. Change your password or keys from Settings at any time.'}),el('a',{class:'button primary',href:'/',text:'Open Aether'})));return;}
 const password=el('input',{id:'wl-password',type:'password',autocomplete:'new-password',minlength:'12',maxlength:'256',required:true});
 const confirmation=el('input',{id:'wl-confirmation',type:'password',autocomplete:'new-password',minlength:'12',maxlength:'256',required:true});
 const provider=el('select',{id:'wl-provider'},...Object.entries(PROVIDERS).map(([id,p])=>el('option',{value:id,text:p.label})));
 const key=el('input',{id:'wl-key',type:'password',autocomplete:'off',maxlength:'512',spellcheck:'false'});
 const keyHint=el('p',{class:'small'}),keyLabel=el('label',{for:'wl-key'});
 const showProvider=()=>{const p=PROVIDERS[provider.value];keyLabel.textContent=p.label+' API key';key.placeholder=p.placeholder;
  keyHint.textContent='Paste the key from '+p.where+'. Aether keeps it encrypted on this computer, gives it to your account and adds the '+p.label+' connection.';};
 provider.onchange=showProvider;showProvider();
 const error=el('p',{class:'form-error',role:'alert'});
 const finish=el('button',{type:'submit',class:'primary',text:'Finish setup'});
 const skip=button('Skip for now',async()=>{
  if(!confirm('Skip the welcome? You keep the default password until you change it under Change password, and you can add your key in Settings → API Keys. The welcome will not come back.'))return;
  try{await api('/api/access/welcome',{action:'skip'});location.assign('/');}catch(e){error.textContent=e.message;}});
 const form=el('form',{class:'wl-form'},
  el('section',{class:'ui-card'},el('h2',{text:'1. Your password'}),
   el('p',{class:'muted',text:'You signed in with the default password, which is the same on every new install. Choose your own now.'}),
   field('New password','At least 12 characters.',password),field('Type it again','',confirmation)),
  el('section',{class:'ui-card'},el('h2',{text:'2. Your API key'}),
   el('p',{class:'muted',text:'Choose who runs your AI models, then paste your key. Leave the key empty to add one later in Settings → API Keys; a model on this computer (LM Studio) needs no key and is added in Settings → Models.'}),
   field('Provider','',provider),el('div',{class:'wl-field'},keyLabel,keyHint,key)),
  error,el('div',{class:'actions'},skip,finish));
 form.onsubmit=async e=>{
  e.preventDefault();error.textContent='';
  if(password.value!==confirmation.value){error.textContent='The two passwords do not match.';return;}
  finish.disabled=true;
  try{
   const value=await api('/api/access/welcome',{action:'finish',password:password.value,confirmation:confirmation.value,provider:provider.value,api_key:key.value});
   password.value=confirmation.value=key.value='';
   form.replaceChildren(el('section',{class:'ui-card'},el('h2',{text:'You are ready'}),
    el('ul',{},el('li',{text:'Your password is changed. Sign in with it from now on.'}),
     el('li',{text:value.connection?'Your '+value.provider+' key is saved and the connection “'+value.connection+'” is ready.':value.key_error?'Your key was not saved: '+value.key_error+' Add it in Settings → API Keys.':'No API key was added. Add one in Settings → API Keys when you have it.'}),
     value.connection?el('li',{text:'When Settings → API Keys asks for your API password, it is this same new password.'}):null),
    el('a',{class:'button primary',href:'/',text:'Open Aether'})));
  }catch(err){error.textContent=err.message;}finally{finish.disabled=false;}
 };
 root.append(form);password.focus();
}
