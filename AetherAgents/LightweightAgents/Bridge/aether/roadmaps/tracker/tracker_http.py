"""One HTTP/data/static contract for standalone and Mission Control surfaces."""
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
try:
    from .tracker_store import DataValidationError,RevisionConflictError
except ImportError:
    from tracker_store import DataValidationError,RevisionConflictError

ROOT=Path(__file__).resolve().parent
MAX_REQUEST_BYTES=2*1024*1024
STATIC={'':'index.html','index.html':'index.html','app.js':'app.js','styles.css':'styles.css','schema.json':'schema.json'}


def send(handler,status,payload,content_type='application/json; charset=utf-8'):
    raw=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode('utf-8')
    # Inside Mission Control, refusals answered before the body is read dispose of it first (S4).
    dispose=getattr(handler,'dispose_request_body',None);linger=dispose() if dispose is not None else False
    handler.send_response(status)
    for k,v in {'Content-Type':content_type,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"}.items():handler.send_header(k,v)
    if linger:handler.send_header('Connection','close')
    handler.end_headers();handler.wfile.write(raw)
    if linger:handler.linger_close()


def handle_tracker(handler,method,*,store,base='/',scope=None,links=(),evidence_base=None):
    path=urlparse(handler.path).path
    if not path.startswith(base):return False
    route=path[len(base):]
    if route not in {*STATIC,'api/data','api/health','favicon.ico'}:return False
    hosts={f'127.0.0.1:{handler.server.server_port}',f'localhost:{handler.server.server_port}'}
    if handler.headers.get('Host') not in hosts or handler.headers.get('Origin') not in (None,'http://'+handler.headers.get('Host','')):
        send(handler,403,{'error':'Use the selected local tracker surface.'});return True
    try:
        if method=='GET' and route=='api/data':
            data,revision=store.read()
            send(handler,200,dict(data=data,revision=revision,scope=dict(label=scope or 'Selected roadmap store',source=str(store.path),
                notice='Roadmap tracking edits do not approve plans or staff, start execution, or accept project closure.'),links=list(links),evidenceBase=evidence_base))
        elif method=='GET' and route=='api/health':send(handler,200,{'status':'ok'})
        elif method=='GET' and route=='favicon.ico':send(handler,204,b'')
        elif method=='GET' and route in STATIC:
            file=ROOT/STATIC[route]
            try:raw=file.read_bytes()
            except OSError:
                send(handler,503,{'error':'Tracker page assets are unavailable. Repair the application installation.'})
                return True
            send(handler,200,raw,(mimetypes.guess_type(file.name)[0] or 'application/octet-stream')+'; charset=utf-8')
        elif method=='PUT' and route=='api/data':
            try:length=int(handler.headers.get('Content-Length','0'))
            except ValueError:length=0
            if not 0<length<=MAX_REQUEST_BYTES:send(handler,413,{'error':'Submit JSON up to 2 MiB'});return True
            if handler.headers.get('Content-Type','').split(';')[0]!='application/json':send(handler,400,{'error':'Submit application/json'});return True
            body=json.loads(handler.rfile.read(length).decode('utf-8'))
            if not isinstance(body,dict) or set(body)!={'data','expectedRevision'} or not isinstance(body['expectedRevision'],str):
                send(handler,400,{'error':'Submit data and its expectedRevision'});return True
            revision=store.write(body['data'],body['expectedRevision']);send(handler,200,{'revision':revision})
        else:send(handler,405,{'error':'Method unavailable'})
    except DataValidationError as exc:send(handler,422,{'error':'Validation failed','details':exc.errors})
    except RevisionConflictError as exc:send(handler,409,{'error':str(exc)})
    except (ValueError,OSError) as exc:send(handler,400,{'error':str(exc)})
    return True
