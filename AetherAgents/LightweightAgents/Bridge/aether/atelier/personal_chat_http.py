"""Own account-scoped personal-cartridge actions and dashboard projections.

Never choose an owner from request data, access other accounts, or auto-publish.
"""
from .chat_briefing import briefing


def source_selection(store, body):
    cid=body.get('chat_id')
    if not cid: return None
    doc=store.read(cid)
    selected=body.get('messages',[])
    if not isinstance(selected,list) or not selected or len(selected)>100 or any(not isinstance(i,str) for i in selected):
        raise ValueError('Select between 1 and 100 messages from your chat.')
    matches=[m for m in doc['messages'] if m['id'] in selected]
    if len(matches)!=len(set(selected)):
        raise ValueError('A selected message is not in this chat.')
    return dict(chat_id=cid,title=doc['title'],messages=[dict(id=m['id'],role=m['role'],model=m.get('model')) for m in matches])


def handle(store, method, query, body=None):
    cartridge=store.cartridge
    if cartridge is None: raise ValueError('Personal cartridges require a signed-in account.')
    if method=='GET':
        if query.get('export')==['1']: return cartridge.export()
        if query.get('briefing')==['1']:
            cid=query.get('chat',[''])[0]
            doc=store.read(cid) if cid else {}
            profile=next((p for k,p in store.settings.read()['document'].get('profiles',{}).items() if k==query.get('model',[''])[0]),{})
            return briefing(cartridge,doc,query.get('query',[''])[0][:100000],budget=context_budget(profile))
        return dict(**cartridge.snapshot(),chats=store.listing(),models=store.models())
    if method=='POST':
        if body.get('action')=='briefing':
            doc=store.read(body['chat']) if body.get('chat') else {}
            text=body.get('query','')
            if not isinstance(text,str) or len(text)>100000: raise ValueError('Preview text must be at most 100,000 characters.')
            profile=store.settings.read()['document'].get('profiles',{}).get(body.get('model'),{})
            return briefing(cartridge,doc,text,budget=context_budget(profile))
        source=source_selection(store,body) if body.get('action')=='save' and not body.get('id') else None
        return cartridge.change(body,source)
    raise ValueError('Unsupported cartridge request.')


def context_budget(profile):
    window=int(profile.get('contextWindow') or 0)
    allowance=max(1000,int((window-int(profile.get('maxOutputTokens') or 4096))*2.5)) if window else 120000
    return min(6000,max(0,(allowance-2000)//4))
