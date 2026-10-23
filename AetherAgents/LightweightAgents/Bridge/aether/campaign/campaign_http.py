"""Campaign state page and API (MVP-01 G2): where the selected campaign is, and what it needs now.

Inputs:  GET /projects/campaign                 the page
         GET /api/campaign-state                the selected campaign's state
         GET /api/campaign-state?id=<campaign>  another campaign's state
Outputs: JSON {campaign_id, stage, condition, reason, actions, sources, signals, obligations}, or the page.
         403: not this server's Host, or another site's request; 404: no such campaign;
         405: any method but GET; 409: no campaign registry on this host; 503: the registry cannot
         be read now (unreadable, being replaced or malformed); 500: anything unexpected, with its
         error text (never a dropped connection).
Never:   writes anything (the projection is read-only and pure); imports Mission Control
         (Mission Control imports this module, never the reverse); answers a path outside its family.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from aether.paths import WEB_DIR

from .records import CampaignNotFound, CampaignRegistryUnavailable, load_campaign_records
from .state import campaign_state
from . import receipts

PAGE = "/projects/campaign"
API = "/api/campaign-state"
PAGE_FILE = WEB_DIR / "campaign.html"
# The candidate export and the journey page belong to the private full edition. Their modules reach the
# Runtime, so they load only when a full-edition request names them (test_v01_public_boundary checks the paths).
CANDIDATES_API = "/api/campaign-candidate"
JOURNEY_PAGE = "/projects/campaign-journey"
JOURNEY_API = "/api/campaign-journey"
PUBLIC_PATHS = (PAGE, API, receipts.PAGE, receipts.API)


def _local(handler) -> bool:
    """Host must be this server; a request that carries an Origin must carry this server's own."""
    origin = f"http://127.0.0.1:{handler.server.server_port}"
    if handler.headers.get("Host") != origin.removeprefix("http://"):
        return False
    if handler.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    return handler.headers.get("Origin") in (None, origin)


def _commission(atelier_root, query):
    """The atelier commission these pages show: the one named, else the latest; None for an earlier campaign."""
    if atelier_root is None:
        return None
    from aether.atelier import projection
    wanted = (parse_qs(query).get("id") or [""])[0].strip()
    if wanted:
        return wanted if projection.exists(atelier_root, wanted) else None
    return projection.latest(atelier_root)


def handle_public_campaign_state(handler, method, atelier_root) -> bool:
    """The Atelier edition: commission state and receipts only, read from the daybook.

    An id that names no commission is a plain 404; it never falls through to the earlier native records."""
    parsed = urlparse(handler.path)
    path = parsed.path
    if path not in PUBLIC_PATHS:
        return False
    if not _local(handler):
        handler.settings_send({"error": "Use the campaign page on this Mission Control."}, 403)
    elif method != "GET":
        handler.settings_send({"error": "Method unavailable"}, 405)
    elif path in (PAGE, receipts.PAGE):
        handler.settings_send((PAGE_FILE if path == PAGE else WEB_DIR / 'campaign_receipts.html').read_bytes(), html=True)
    else:
        from aether.atelier import projection
        try:
            commission = _commission(atelier_root, parsed.query)
            if commission is None:
                handler.settings_send({"error": "No such commission."}, 404)
                return True
            view = (projection.campaign_state if path == API else projection.campaign_receipts)(atelier_root, commission)
        except Exception as exc:     # noqa: BLE001 - answer with the error, never drop the request
            handler.settings_send({"error": f"The commission could not be read: {type(exc).__name__}: {exc}"}, 500)
        else:
            handler.settings_send(view)
    return True


def handle_campaign_state(handler, method, registry, *, live_runs=None, load=load_campaign_records,
                          project=campaign_state, atelier_root=None) -> bool:
    """registry: the Mission Control project registry (anything with .registry_path), or None.
    live_runs: a callable returning the run IDs in flight in this process (optional).
    load and project are the loader and the projection; tests may pass stubs.
    atelier_root: where the atelier keeps its daybooks. A commission's state and receipt are read-only views of its
    daybook and facts (aether.atelier.projection); an id that names no commission reads the earlier records."""
    parsed = urlparse(handler.path)
    path = parsed.path
    if path not in (PAGE, API, receipts.PAGE, receipts.API, receipts.RECORD_API, CANDIDATES_API, JOURNEY_PAGE, JOURNEY_API):
        return False
    from . import candidates, journey   # full edition only (see CANDIDATES_API)
    commission = None
    if path in (API, receipts.API) and method == "GET" and _local(handler):
        try:
            commission = _commission(atelier_root, parsed.query)
        except Exception as exc:     # noqa: BLE001 - answer with the error, never drop the request
            handler.settings_send({"error": f"The commission could not be read: {type(exc).__name__}: {exc}"}, 500)
            return True
    if not _local(handler):
        handler.settings_send({"error": "Use the campaign page on this Mission Control."}, 403)
    elif method != "GET":
        handler.settings_send({"error": "Method unavailable"}, 405)
    elif path in (PAGE, receipts.PAGE, journey.PAGE):
        page = {PAGE: PAGE_FILE, receipts.PAGE: WEB_DIR / 'campaign_receipts.html', journey.PAGE: WEB_DIR / 'campaign_journey.html'}[path]
        handler.settings_send(page.read_bytes(), html=True)
    elif commission is not None:
        from aether.atelier import projection
        try:
            view = (projection.campaign_state if path == API else projection.campaign_receipts)(atelier_root, commission)
        except Exception as exc:     # noqa: BLE001 - answer with the error, never drop the request
            handler.settings_send({"error": f"The commission could not be projected: {type(exc).__name__}: {exc}"}, 500)
        else:
            handler.settings_send(view)
    elif registry is None:
        handler.settings_send({"error": "No campaign registry is configured on this host."}, 409)
    else:
        wanted = (parse_qs(parsed.query).get("id") or [""])[0].strip() or None
        try:
            runs = tuple(live_runs() if live_runs else ())
            if path in (API, journey.API):
                records = load(registry.registry_path, wanted, live_runs=runs)
                body = project(records) if path == API else journey.project(records)
                if path == API:
                    from urllib.parse import urlencode
                    body = dict(body, actions=[*body['actions'], dict(label='Campaign steps and prerequisites',
                        href=journey.PAGE+'?'+urlencode(dict(id=body['campaign_id'])))])
            else:
                records = load_campaign_records(registry.registry_path, wanted, live_runs=runs, include_receipts=True)
                if path == candidates.API:
                    query = parse_qs(parsed.query)
                    try:
                        key=(query.get('sha256') or [''])[0]
                        raw=candidates.export_candidate(records,(query.get('slot') or [''])[0],key)
                    except ValueError as exc:
                        handler.settings_send({'error':str(exc)},409);return True
                    handler.send_response(200)
                    for name,value in [('Content-Type','application/zip'),('Content-Length',str(len(raw))),
                        ('Content-Disposition',f'attachment; filename="candidate-{key}.zip"'),
                        ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Connection','close')]:
                        handler.send_header(name,value)
                    handler.close_connection=True;handler.end_headers();handler.wfile.write(raw);return True
                body = receipts.campaign_receipts(records)
                if path == receipts.RECORD_API:
                    query = parse_qs(parsed.query)
                    selected = next((row for row in body['receipts']
                                     if row['path'] == (query.get('path') or [''])[0]
                                     and row['sha256'] == (query.get('sha256') or [''])[0]), None)
                    if selected is None:
                        raise CampaignNotFound('Record is missing or changed; refresh the campaign receipt')
                    # The selected path is matched against loaded records, never opened from HTTP input.
                    body = dict(campaign_id=body['campaign_id'], record=selected,
                                content=records['receipt_documents'].get(selected['path']),
                                content_scope='Parsed record; registry content is limited to the selected campaign entry',
                                verification=body['verification'])
        except CampaignNotFound as exc:
            handler.settings_send({"error": str(exc.args[0] if exc.args else exc)}, 404)
        except CampaignRegistryUnavailable as exc:
            handler.settings_send({"error": str(exc), "retry": True}, 503)
        except Exception as exc:     # noqa: BLE001 - answer with the error, never drop the request
            handler.settings_send({"error": f"The campaign state could not be computed: "
                                            f"{type(exc).__name__}: {exc}"}, 500)
        else:
            handler.settings_send(body)
    return True
