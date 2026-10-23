"""Project-scoped roadmap tracking through the Bridge's Roadmap Tracker implementation."""
from pathlib import Path
from urllib.parse import urlparse,parse_qs,quote
from aether.roadmaps.projects import ProjectRegistry
from aether.roadmaps.project_setup import checked_path
from aether.roadmaps.tracker import tracker_http,tracker_store


def tracker():
    """The one Roadmap Tracker implementation (aether.roadmaps.tracker), shared with Projects."""
    return tracker_http,tracker_store


class ProjectRoadmaps:
    def __init__(self,registry,*,approval_reader=None):
        self.registry=registry
        self.approval_reader=approval_reader

    def workspace(self,pid):
        current=ProjectRegistry(self.registry.lightweight_dir,self.registry.registry_path);current.load();project=current.get(pid)
        if not project.framing:raise ValueError('Select a framed project for project-local roadmaps')
        path=checked_path(Path(project.root)/'.aether/roadmaps.json')
        http,store=tracker();encoded=quote(pid,safe='')
        links=[dict(label=label,href=url+encoded) for label,url in (
            ('Publish approved plan','/projects/roadmap-publication?project_id='),
            ('Project setup','/projects/lead?project_id='),('Plan decisions','/projects/plan-ruling?project_id='),
            ('Staffing','/projects/staffing?project_id='),('Execution','/projects/execution?project_id='))]
        return http,store.RoadmapStore(path),'Project roadmap tracking: '+project.name,links


def handle_project_roadmaps(handler,method,service):
    parsed=urlparse(handler.path);path=parsed.path
    if path!='/projects/roadmaps' and not path.startswith('/roadmaps/'):return False
    try:
        if service is None:raise ValueError('Project roadmap service is unavailable on this host')
        if path=='/projects/roadmaps':
            if method!='GET':handler.settings_send({'error':'Use GET for the roadmap page'},405);return True
            pid=parse_qs(parsed.query).get('project_id',[''])[0];service.workspace(pid)
            handler.send_response(303);handler.send_header('Location','/roadmaps/'+quote(pid,safe='')+'/');handler.send_header('Content-Length','0');handler.end_headers();return True
        pieces=path.split('/')
        if len(pieces)<4:raise ValueError('Use the project Roadmaps link')
        pid=pieces[2];http,store,scope,links=service.workspace(pid)
        if not http.handle_tracker(handler,method,store=store,base='/roadmaps/'+pid+'/',scope=scope,links=links,
                evidence_base='/projects/roadmap-evidence?project_id='+quote(pid,safe='')):handler.settings_send({'error':'Roadmap resource unavailable'},404)
    except (ValueError,OSError,KeyError) as exc:handler.settings_send({'error':str(exc)},409)
    return True
