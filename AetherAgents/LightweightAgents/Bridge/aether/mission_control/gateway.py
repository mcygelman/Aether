"""Mission Control gateway (MOD-14, Bridge Overhaul M5, first step): every route in one table,
one local-origin guard in front of all of them.

Mission Control grew route by route: its own routes in server.py, and some twenty-four
modules that each answer a family of paths. Most of those modules check the request's Host
and Origin themselves; Mission Control's own routes (missions, dispatch, execution policy,
hiring, heavyweight runs, journal undo, project removal) did not, so any web page the
operator visited could post to them. The guard here runs first, for every route.

Inputs:  the request method, its path and headers, and the server's port.
Outputs: ROUTES (methods, path or prefix, owning module, whether it changes state, page);
         route_for(method, path) -> Route or None; refuse(handler, method) -> True when it has
         already answered (403: not this local server or not a page it served; 404: not a
         declared route).
Never:   lets a request through unless its Host is this server (127.0.0.1:<port>); lets a
         request through that carries another site's Origin (or "null"), or that the browser
         marks cross-site; serves a path no Route declares.
Types:   request bodies of Mission Control's own state-changing routes are typed in route_types.TYPES
         (checked in server.py's do_POST after its project guards; 400 with plain-English problems).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse

G, P, U, D = "GET", "POST", "PUT", "DELETE"
SERVER = "mission_control.server"


@dataclass(frozen=True)
class Route:
    methods: Tuple[str, ...]
    path: str
    owner: str                    # the module that answers it
    changes_state: bool = False
    prefix: bool = False          # path is a prefix: the owner answers everything below it
    page: bool = False            # an HTML page a person opens


def _family(api: str, page: str, owner: str, methods=(G, P)) -> Tuple[Route, ...]:
    """A module that answers its own API family and serves its own page."""
    return (Route(methods, api, owner, changes_state=P in methods or U in methods, prefix=True),
            Route((G,), page, owner, page=True))


ROUTES: Tuple[Route, ...] = (
    Route((G,), '/agent-editor/', 'agent_editor.http', prefix=True, page=True),
    Route((G, P), '/api/heavyweight-editor/', 'runtime.sessions.heavyweight_editor_http', changes_state=True, prefix=True),
    Route((G,), '/api/heavyweight-editor/options', 'runtime.sessions.heavyweight_editor_http'),
    Route((P,), '/api/heavyweight-editor/inspect', 'runtime.sessions.heavyweight_editor_http'),
    Route((P,), '/api/heavyweight-editor/prepare', 'runtime.sessions.heavyweight_editor_http', changes_state=True),
    Route((P,), '/api/heavyweight-editor/acquire', 'runtime.sessions.heavyweight_editor_http', changes_state=True),
    *(Route((P,), '/api/heavyweight-editor/'+action, 'runtime.sessions.heavyweight_editor_http', changes_state=True)
      for action in ('launch','recover-call','seal','resume','hydrate','complete-runtime','resume-runtime','enroll')),
    Route((P,), '/api/agent-editor/validate', 'agent_editor.http'),
    Route((P,), '/api/agent-editor/export', 'agent_editor.http'),
    Route((G,), '/api/agent-editor/consent-policy', 'agent_editor.consent_http'),
    Route((P,), '/api/agent-editor/consent-offer', 'agent_editor.consent_http'),
    Route((G,), '/api/agent-editor/projects', 'agent_editor.publication_http'),
    Route((P,), '/api/agent-editor/publication-preview', 'agent_editor.publication_http'),
    Route((P,), '/api/agent-editor/publication-confirm', 'agent_editor.publication_http', changes_state=True),
    Route((P,), '/api/agent-editor/publication-inspect', 'agent_editor.publication_http'),
    *_family('/api/continuation-recovery/','/projects/batch-recovery','runtime.sessions.continuation_recovery_http'),
    *_family('/api/owner-memory-recovery/','/projects/owner-memory-recovery','runtime.sessions.owner_memory_recovery_http'),
    # Mission Control's own routes (server.py)
    Route((G,), "/", SERVER, page=True),
    Route((G,), "/api/status", SERVER),
    Route((G,), "/api/health", SERVER),
    Route((G,), "/api/pool", SERVER),
    Route((G,), "/api/journal", SERVER),
    Route((P,), "/api/journal/", SERVER, changes_state=True, prefix=True),          # <id>/undo
    Route((G,), "/api/board", SERVER),
    Route((G, P), "/api/board/", SERVER, changes_state=True, prefix=True),          # <id>, <id>/reopen
    Route((G, P), "/api/projects", SERVER, changes_state=True),
    Route((P,), "/api/projects/select", SERVER, changes_state=True),
    Route((D,), "/api/projects/", SERVER, changes_state=True, prefix=True),
    Route((P,), "/api/hire", SERVER, changes_state=True),
    Route((G, P), "/api/execution-policy", SERVER, changes_state=True),
    Route((G, P), "/api/missions", SERVER, changes_state=True),
    Route((G, P), "/api/missions/", SERVER, changes_state=True, prefix=True),       # launch, dispatch, stream...
    Route((P,), "/api/agents/", SERVER, changes_state=True, prefix=True),           # validate, ack-test
    Route((G, P), "/api/heavyweight-runs", SERVER, changes_state=True),
    Route((G, P), "/api/heavyweight-runs/", SERVER, changes_state=True, prefix=True),  # active, events, pause...
    # Models & Deployments settings (backends.model_settings_http)
    # G9 runtime admission: observe writes RuntimeEvidence; admit writes host files on confirmation.
    Route((P,), "/settings/models-context/api/observe", "backends.model_settings_http", changes_state=True),
    Route((P,), "/settings/models-context/api/admit", "backends.model_settings_http", changes_state=True),
    # G9 lane D: deployment admission for all campaign roles (observe, review, confirm, inspect, recover,
    # hosts-review/-confirm, renew-, reidentify-). POST only; confirmations write the registry and host files.
    Route((P,), "/settings/models-context/api/deployment/", "backends.model_settings_http", changes_state=True,
          prefix=True),
    Route((G, P, D), "/settings/models-context/", "backends.model_settings_http", changes_state=True, prefix=True),
    Route((G,), "/settings/models-context", "backends.model_settings_http"),       # redirects to the slash
    Route((G,), "/projects/overseer", "mission_control.overseer_host_http", page=True),
    Route((G,), "/api/overseer-conversation", "mission_control.overseer_host_http"),
    Route((G,), "/api/overseer-conversation/records", "mission_control.overseer_host_http"),
    Route((P,), "/api/overseer-conversation/send", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/refresh", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/close-unsubmitted", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/cancel-submission", "mission_control.overseer_host_http", changes_state=True),
    Route((G,), "/api/overseer-conversation/allowance", "mission_control.overseer_host_http"),
    Route((P,), "/api/overseer-conversation/allowance/preview", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/allowance/confirm", "mission_control.overseer_host_http", changes_state=True),
    Route((G,), "/api/overseer-conversation/plans", "mission_control.overseer_host_http"),
    Route((P,), "/api/overseer-conversation/plans/preview", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/plans/confirm", "mission_control.overseer_host_http", changes_state=True),
    Route((G,), "/api/overseer-conversation/successor", "mission_control.overseer_host_http"),
    Route((P,), "/api/overseer-conversation/successor/preview", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/successor/confirm", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/recovery/inspect", "mission_control.overseer_host_http"),
    Route((P,), "/api/overseer-conversation/recovery/preview", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/recovery/confirm", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/recovery/hardware-preview", "mission_control.overseer_host_http", changes_state=True),
    Route((P,), "/api/overseer-conversation/recovery/hardware-confirm", "mission_control.overseer_host_http", changes_state=True),
    # The atelier (Fable's design, 10-18): one thread per commission; open, read, post, wake, files by hash.
    Route((G,), "/projects/atelier", "atelier.atelier_http", page=True),
    Route((G,), "/projects/places", "atelier.workplace_http", page=True),
    Route((G,), "/api/places", "atelier.workplace_http"),
    Route((P,), "/api/places/join", "atelier.workplace_http", changes_state=True),
    Route((P,), "/api/places/start", "atelier.workplace_http", changes_state=True),
    Route((P,), "/api/places/stop", "atelier.workplace_http", changes_state=True),
    Route((P,), "/api/places/prepare-library", "atelier.workplace_http", changes_state=True),
    Route((G,), "/api/atelier", "atelier.atelier_http"),
    Route((G,), "/api/atelier/read", "atelier.atelier_http"),
    Route((G,), "/api/atelier/file", "atelier.atelier_http"),
    Route((P,), "/api/atelier/open", "atelier.atelier_http", changes_state=True),
    Route((P,), "/api/atelier/post", "atelier.atelier_http", changes_state=True),
    Route((P,), "/api/atelier/wake", "atelier.atelier_http", changes_state=True),
    # Projects and roadmaps
    Route((G, P, U), "/api/project-folders", "roadmaps.project_folders", changes_state=True, prefix=True),
    Route((G, P, U), "/project-folders/", "roadmaps.project_folders", changes_state=True, prefix=True, page=True),
    Route((G,), "/projects/roadmaps", "roadmaps.project_roadmaps", page=True),
    # The shared tool registry (Bridge Overhaul M2 management view)
    *_family("/api/tool-registry/", "/projects/tools", "mission_control.tool_registry_http"),
    # Campaign (MOD-17): stage and condition of the selected campaign (read-only). Exact routes,
    # not a family, so nothing else under the prefix is admitted.
    Route((G,), "/api/campaign-state", "campaign.campaign_http"),
    Route((G,), "/api/campaign-journey", "campaign.campaign_http"),
    Route((G,), "/projects/campaign-journey", "campaign.campaign_http", page=True),
    Route((G,), "/api/campaign-receipts", "campaign.campaign_http"),
    Route((G,), "/api/campaign-receipt-record", "campaign.campaign_http"),
    Route((G,), "/api/campaign-candidate", "campaign.campaign_http"),
    Route((G,), "/projects/campaign-receipts", "campaign.campaign_http", page=True),
    Route((G,), "/projects/campaign", "campaign.campaign_http", page=True),
    Route((G, P, U), "/roadmaps/", "roadmaps.project_roadmaps", changes_state=True, prefix=True, page=True),
    *_family("/api/plan-ruling/", "/projects/plan-ruling", "roadmaps.plan_rulings_http"),
    *_family("/api/operator-decisions/", "/projects/operator-decisions", "roadmaps.operator_decisions_http"),
    *_family("/api/project-lead/", "/projects/lead", "roadmaps.project_engagement"),
    Route((G,), "/projects/brief", "roadmaps.project_engagement", page=True),
    *_family("/api/heavyweight-role/", "/projects/heavyweight-role", "runtime.staffing.heavyweight_role_http"),
    *_family("/api/project-plan/", "/projects/plan", "roadmaps.project_planning"),
    Route((G, P), '/api/project-model-restrictions/', 'roadmaps.model_restrictions_http', changes_state=True, prefix=True),
    Route((G,), '/api/project-model-restrictions/options', 'roadmaps.model_restrictions_http'),
    *(Route((P,), '/api/project-model-restrictions/'+action, 'roadmaps.model_restrictions_http',
            changes_state=action=='confirm') for action in ('preview','confirm','inspect')),
    *_family("/api/project-setup/", "/projects/new", "roadmaps.project_setup"),
    *_family("/api/roadmap-evidence", "/projects/roadmap-evidence", "roadmaps.roadmap_evidence_http"),
    *_family("/api/roadmap-publication/", "/projects/roadmap-publication", "roadmaps.roadmap_publication_http"),
    # MVP-01 operator routes (no test stands in for the operator). Exact, so each is declared with its
    # method; their families below already admit them. Bodies are typed by their owning modules
    # (exact field sets), not route_types, which types only server.py's own routes.
    Route((G,), "/api/reviewer-admission/assessors", "runtime.review_closure.reviewer_console_http"),
    Route((P,), "/api/reviewer-admission/assessors-preview", "runtime.review_closure.reviewer_console_http",
          changes_state=True),
    Route((P,), "/api/reviewer-admission/assessors-confirm", "runtime.review_closure.reviewer_console_http",
          changes_state=True),   # writes reviewer.accepted_assessors (restart required)
    Route((P,), "/api/reviewer-admission/assessment-preview", "runtime.review_closure.reviewer_console_http",
          changes_state=True),
    Route((P,), "/api/reviewer-admission/assessment-confirm", "runtime.review_closure.reviewer_console_http",
          changes_state=True),   # records the Minder first-session assessment
    Route((P,), "/api/planning-host/assessment-preview", "runtime.orchestration.planning_host_http",
          changes_state=True),
    Route((P,), "/api/planning-host/assessment-confirm", "runtime.orchestration.planning_host_http",
          changes_state=True),   # records the planning completion assessment (RuntimeEvidence)
    *(Route((P,), "/api/native-contribution-host/" + action, "runtime.review_closure.native_contribution_host_http",
            changes_state=True)   # publication of a held contribution and the task decision
      for action in ("preview-publication", "confirm-publication", "publication-state",
                     "preview-task-decision", "confirm-task-decision")),
    # Runtime: orchestration, review and closure, staffing
    *_family("/api/charter/", "/chartered-project", "runtime.orchestration.charter_http"),
    *_family("/api/planning-host/", "/projects/proposals", "runtime.orchestration.planning_host_http"),
    *_family("/api/staff-execution/", "/projects/execution", "runtime.orchestration.staff_execution_http"),
    *_family("/api/closure/", "/projects/closure", "runtime.review_closure.closure_packets_http"),
    *_family("/api/closure-dialogue/", "/projects/closure-dialogue", "runtime.review_closure.native_closure_http"),
    *_family("/api/minder-first-session/", "/projects/minder-first-session",
             "runtime.review_closure.minder_first_session_http"),
    *_family("/api/minder-host/", "/projects/minder-run", "runtime.review_closure.minder_host_http"),
    *_family("/api/minder-preparation/", "/projects/minder", "runtime.review_closure.minder_preparation_http"),
    *_family("/api/minder-qualification/", "/projects/minder-qualification",
             "runtime.review_closure.minder_qualification_http"),
    *_family("/api/native-contribution-host/", "/projects/contribution-launch",
             "runtime.review_closure.native_contribution_host_http"),
    *_family("/api/reviewer-admission/", "/projects/reviewer-admission",
             "runtime.review_closure.reviewer_console_http"),
    *_family("/api/native-review-host/", "/projects/review-launch", "runtime.review_closure.reviewer_host_http"),
    *_family("/api/desktop-staffing/", "/projects/staffing-launch", "runtime.staffing.desktop_staffing_http"),
    *_family("/api/native-staff-decisions/", "/projects/staff-decisions", "runtime.staffing.native_staff_console"),
    *_family("/api/staffing/", "/projects/staffing", "runtime.staffing.staffing_http"),
)


# The Atelier edition (v0.1, Michel 10-22): the routes of the public surfaces only. Every other route,
# including the private full edition's, is undeclared there and answers 404 before any module sees it.
ATELIER_SERVER = "mission_control.atelier_server"
_PUBLIC_OWNERS = frozenset({"atelier.atelier_http", "atelier.workplace_http", "roadmaps.project_folders",
                            "roadmaps.project_roadmaps", "roadmaps.project_setup", "mission_control.tool_registry_http",
                            "backends.model_settings_http", "agent_editor.http"})
_PUBLIC_CAMPAIGN = frozenset({"/api/campaign-state", "/api/campaign-receipts", "/projects/campaign",
                              "/projects/campaign-receipts"})
PUBLIC_ROUTES: Tuple[Route, ...] = (
    Route((G,), "/", ATELIER_SERVER, page=True),
    Route((G,), "/projects", ATELIER_SERVER, page=True),
    Route((G,), "/api/health", ATELIER_SERVER),
    *(r for r in ROUTES if r.owner in _PUBLIC_OWNERS),
    *(r for r in ROUTES if r.owner == "campaign.campaign_http" and r.path in _PUBLIC_CAMPAIGN),
    Route((G,), "/agents", "memory.agent_view_http", page=True),
    Route((G,), "/api/agents", "memory.agent_view_http"),
    Route((G,), "/api/agents/", "memory.agent_view_http", prefix=True),
)


def route_for(method: str, path: str, routes: Tuple[Route, ...] = ROUTES) -> Optional[Route]:
    """The declared route for this request, exact paths before prefixes (longest first)."""
    for route in routes:
        if not route.prefix and route.path == path and method in route.methods:
            return route
    matches = [r for r in routes if r.prefix and method in r.methods
               and (path.startswith(r.path) or path == r.path.rstrip("/"))]
    return max(matches, key=lambda r: len(r.path), default=None)


def local_origin(handler) -> str:
    return f"http://127.0.0.1:{handler.server.server_port}"


def refuse(handler, method: str, routes: Tuple[Route, ...] = ROUTES) -> bool:
    """Answer and return True unless the request is local and declared.

    Browsers always send Origin with a POST, so another site's request is refused by its Origin
    (or by Sec-Fetch-Site), and a rebound DNS name by its Host. A request with no Origin at all
    is a local tool, not a browser page; it is let through, as charter_http always allowed.
    Modules that require their own page's Origin still check it after this.
    """
    origin = local_origin(handler)
    sent = handler.headers.get("Origin")
    if handler.headers.get("Host") != origin.removeprefix("http://"):
        return _answer(handler, 403, "Open Mission Control at " + origin)
    if (sent is not None and sent != origin) or handler.headers.get("Sec-Fetch-Site") == "cross-site":
        return _answer(handler, 403, "Use Mission Control's own pages; requests from other sites are refused")
    path = urlparse(handler.path).path
    if route_for(method, path, routes) is None:
        # A declared path asked with a method it does not take is 405, as its own module says.
        known = any(route_for(m, path, routes) for m in (G, P, U, D))
        if known:
            return _answer(handler, 405, "method not allowed")
        if routes is not ROUTES and route_for(method, path) is not None:
            # A page or action of the full edition: still 404 here, said plainly for the page asking.
            return _answer(handler, 404, "This part of Mission Control is in the full edition, not this one.")
        return _answer(handler, 404, "not found")
    return False


DRAIN_BYTES = 16 * 1024 * 1024   # a refused body larger than this is left; the connection closes


def _answer(handler, status: int, error: str) -> bool:
    # Read a refused request's body first: otherwise Windows may abort the connection while the
    # client is still sending, and it sees a reset instead of the refusal (10-08 run 8).
    # Mission Control's handler disposes of it in send_json (bounded, with a deadline; S4).
    if getattr(handler, "dispose_request_body", None) is not None:
        handler.send_json({"error": error}, status)
        return True
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if 0 < length <= DRAIN_BYTES:
        handler.rfile.read(length)
    handler.send_json({"error": error}, status)
    return True
