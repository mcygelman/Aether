/* Server-held capabilities; the browser persists only the selected opaque ID. */
'use strict';
window.addEventListener('DOMContentLoaded', () => {
  const el = name => document.getElementById('desk-' + name);
  const labels = {acquire:'Reserve this reviewed runtime', launch:'Launch this one exchange', 'recover-call':'Recover the original returned answer',
    seal:'Confirm these exact terms and seal', resume:'Resume the committed seal', hydrate:'Load the welcomed identity once',
    'complete-runtime':'Complete this original runtime exchange', 'resume-runtime':'Resume the committed runtime completion',
    enroll:'Enroll this welcomed identity for project work'};
  const key = 'aether-heavyweight-desk-request-v1';
  let current = null, busy = false, available = false;
  const query=new URLSearchParams(location.search),profileId=query.get('profile_id'),projectId=query.get('project_id');
  const continuation=!!profileId && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(profileId);
  if(continuation){
    el('continuation').hidden=false;el('continuation-owner').textContent='Requested identity: '+profileId;
    el('return-project').href='/projects/lead?project_id='+encodeURIComponent(projectId||'');
    el('return-brief').href='/projects/brief?project_id='+encodeURIComponent(projectId||'');
    el('return-project').hidden=!projectId;el('return-brief').hidden=!projectId;
  }
  const say = text => { el('status').textContent = text; };
  function clearReview() {
    current = null; el('ack').checked = false; el('ack').disabled = true;
    el('actions').replaceChildren(); el('review').textContent = 'Inspect the selected request for a current review.';
    el('next').replaceChildren();
  }
  function controls() {
    el('refresh').disabled = busy; el('request').disabled = busy;
    el('inspect').disabled = busy || !el('request').value;
    el('ack').disabled = busy || !current || !Object.keys(current.actions).length;
    el('actions').querySelectorAll('button').forEach(button => { button.disabled = busy || !el('ack').checked; });
    el('next').disabled = busy || !current;
    el('welcome-ack').disabled=busy||!available;
    el('welcome-prepare').disabled=busy||!available||!continuation||!el('welcome-ack').checked;
  }
  async function api(action, body) {
    const response = await fetch('/api/heavyweight-editor/' + action, body === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), cache:'no-store'});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'The lifecycle request could not be completed.');
    return value;
  }
  function show(value) {
    if (value.request_id !== el('request').value) throw new Error('Response belongs to another request. Inspect again.');
    clearReview(); current = value;
    const display = {...value}; delete display.actions;
    el('review').textContent = JSON.stringify(display, null, 2);
    for (const action of Object.keys(value.actions)) {
      if (!labels[action]) continue;
      const button = document.createElement('button'); button.type='button'; button.className='btn btn-primary';
      button.textContent=action==='enroll' && value.enrollment_review?.roster_reconciliation_required
        ? 'Confirm the revised roster entry' : labels[action]; button.dataset.action=action;
      button.addEventListener('click', () => act(action)); el('actions').appendChild(button);
    }
    const next=value.next_preparations || {};
    function input(labelText,limit) {
      const label=document.createElement('label');label.textContent=labelText;
      const field=document.createElement('textarea');field.className='form-control';field.rows=3;field.maxLength=limit;
      label.appendChild(field);el('next').appendChild(label);return field;
    }
    function button(label,selection) {
      const control=document.createElement('button');control.type='button';control.className='btn btn-secondary';control.textContent=label;
      control.addEventListener('click',()=>prepareNext(selection()));el('next').appendChild(control);
    }
    if (next.questions) {
      const answers=next.questions.map(question=>input(question,8192));
      button('Prepare answers for review',()=>({kind:'founding',selection:{request_id:value.request_id,operator_answers:answers.map(field=>field.value)}}));
    } else if (next.correction) {
      button('Prepare the permitted answer correction',()=>({kind:'founding',selection:{request_id:value.request_id,operator_answers:null}}));
    }
    if (next.welcome) button('Prepare welcome review',()=>({kind:'welcome',selection:{profile_id:next.welcome}}));
    if (next.revision) {
      const instruction=input('What would you like the identity to reconsider about its succession terms?',8192);
      button('Prepare succession-terms review',()=>({kind:'revision',selection:{welcome_request_id:next.revision,instruction:instruction.value}}));
    }
    say(value.publication_status || value.status);
    controls();
  }
  async function inspect() {
    clearReview(); busy=true; controls();
    try { show(await api('inspect',{request_id:el('request').value})); }
    catch (error) { say(error.message); }
    finally { busy=false; controls(); }
  }
  async function refresh() {
    clearReview(); busy=true; controls();
    try {
      const selected=el('request').value || sessionStorage.getItem(key);
      const value=await api('options'); available=value.available===true && value.acquisition_available===true; el('request').replaceChildren();
      for (const row of value.requests || []) {
        const option=document.createElement('option'); option.value=row.request_id;
        option.textContent=row.kind + ' · ' + (row.model.model_id || 'held') + ' · ' + row.request_id;
        el('request').appendChild(option);
      }
      if ([...el('request').options].some(o => o.value===selected)) el('request').value=selected;
      say(value.available ? (value.requests.length ? 'Select and inspect a prepared request.' : 'No calls have been staged by the installation host.') : value.error);
    } catch (error) { available=false; say(error.message); }
    finally { busy=false; controls(); }
  }
  async function act(action) {
    if (busy || !current || !el('ack').checked) return;
    const body={request_id:current.request_id,review_sha256:current.actions[action],confirmed:true};
    // Persist the ID before sending. No answer, terms, private context or authority
    // token is stored in browser storage, and there is no automatic retry.
    try { sessionStorage.setItem(key,body.request_id); }
    catch (_) { say('Browser recovery storage is unavailable. Keep the request ID and inspect it after any interruption.'); return; }
    clearReview(); busy=true; controls(); say('Waiting for the original lifecycle action…');
    try { show(await api(action,body)); }
    catch (error) { say(error.message + ' Inspect this same request before taking another action.'); }
    finally { busy=false; controls(); }
  }
  async function prepareNext(body,fromProject=false) {
    if (busy || (!current && !fromProject)) return;
    if(fromProject && (!continuation||!available||!el('welcome-ack').checked))return;
    el('welcome-ack').checked=false;
    clearReview();busy=true;controls();say('Preparing the next review…');
    try {
      const value=await api('prepare',body);
      // Only the new opaque ID survives navigation; answers/instructions stay out of storage.
      try {sessionStorage.setItem(key,value.request_id);}
      catch (_) {say('Prepared request '+value.request_id+'. Keep this ID and select it after refreshing.');return;}
      const option=document.createElement('option');option.value=value.request_id;
      option.textContent=value.kind+' · '+(value.model.model_id || 'held')+' · '+value.request_id;
      el('request').appendChild(option);el('request').value=value.request_id;show(value);
    } catch (error) {say(error.message+' Inspect the original request before preparing again.');}
    finally {busy=false;controls();}
  }
  el('refresh').addEventListener('click',refresh); el('inspect').addEventListener('click',inspect);
  el('request').addEventListener('change',() => {clearReview(); controls();});
  el('ack').addEventListener('change',controls);
  el('welcome-ack').addEventListener('change',controls);
  el('welcome-prepare').addEventListener('click',()=>prepareNext({kind:'welcome',selection:{profile_id:profileId}},true));
  refresh();
});
