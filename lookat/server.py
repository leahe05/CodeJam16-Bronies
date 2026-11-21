"""The one web server for LookAt: serves Lea's pages and the JSON API (docs/API.md).

Both the fake backend (lookat/fake_api.py) and the real program (main.py) use
make_app(backend), so the URLs and JSON are identical: a page that works with
the fake works with the real thing.

backend must have:
    lock                         a threading.Lock held while reading/changing it
    screen_json() nurse_json() panel_json()          -> dicts (docs/API.md)
    nurse_action(path, body) demo_action(body)       -> {"ok": ...}

Finding Lea's pages (first match wins; she can use any of these layouts):
    web/dist/<name>.html, web/dist/<name>/index.html    (output of a build tool)
    web/<name>.html,      web/<name>/index.html          (plain files)
If her page doesn't exist yet, lookat/fallback/<name>.html is served instead
(a bare team test page, not a design). Her files always win, nothing to switch.
Static files: web/ at /web/..., and web/dist/assets at /assets/... (Vite-style builds).
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.environ.get("LOOKAT_WEB_DIR") or os.path.join(ROOT, "web")
FALLBACK_DIR = os.path.join(ROOT, "lookat", "fallback")
PAGES = ("patient", "nurse", "panel")
API_FOR_PAGE = {"patient": "/api/screen", "nurse": "/api/nurse", "panel": "/api/panel"}


def find_page(name: str, web_dir: str = WEB_DIR, fallback_dir: str = FALLBACK_DIR):
    """Path of the page to serve for /<name>, and whether it is Lea's (True) or the fallback."""
    for base in (os.path.join(web_dir, "dist"), web_dir):
        for path in (os.path.join(base, f"{name}.html"), os.path.join(base, name, "index.html")):
            if os.path.isfile(path):
                return path, True
    path = os.path.join(fallback_dir, f"{name}.html")
    return (path if os.path.isfile(path) else None), False


def public(data):
    """Drop private keys (starting with _) before sending JSON."""
    if isinstance(data, dict):
        return {k: public(v) for k, v in data.items() if not str(k).startswith("_")}
    if isinstance(data, list):
        return [public(v) for v in data]
    return data


def safe(handler):
    """Bad input (e.g. "setpoint_c": "abc") -> {"ok": false}, never a server error."""
    try:
        return handler()
    except (TypeError, ValueError, KeyError) as e:
        return {"ok": False, "error": f"bad value: {e}"}


def make_app(backend, title: str = "LookAt", web_dir: str = WEB_DIR):
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.concurrency import run_in_threadpool

    app = FastAPI(title=title)
    # Lea may develop with her own dev server (another port) calling this API.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

    os.makedirs(web_dir, exist_ok=True)
    assets = os.path.join(web_dir, "dist", "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    app.mount("/web", StaticFiles(directory=web_dir), name="web")

    def page(name):
        path, _ = find_page(name, web_dir)
        if path:
            return FileResponse(path, headers={"Cache-Control": "no-store"})
        api = API_FOR_PAGE[name]
        return HTMLResponse(f"<h1>No {name} page yet</h1><p>Data: <a href='{api}'>{api}</a> (docs/API.md)</p>")

    from fastapi.responses import RedirectResponse

    @app.get("/patient")
    def patient(request: Request):
        # main.py --gaze web (the default): /patient opens with the page's own eye tracking (WebGazer)
        if getattr(backend, "gaze_mode", None) == "web" and "gaze" not in request.query_params:
            q = "&".join(f"{k}={v}" for k, v in request.query_params.items())
            return RedirectResponse("/patient?gaze=web" + ("&" + q if q else ""))
        return page("patient")

    for name in PAGES:
        if name != "patient":
            app.add_api_route(f"/{name}", (lambda n: lambda: page(n))(name), methods=["GET"])

    @app.get("/")
    def index():
        links = "".join(f"<li><a href='/{n}'>/{n}</a> ({'Lea' if find_page(n, web_dir)[1] else 'test page'})</li>"
                        for n in PAGES)
        return HTMLResponse(f"<h1>LookAt</h1><ul>{links}</ul>")

    @app.get("/api/screen")
    def api_screen():
        with backend.lock:
            return public(backend.screen_json())

    @app.get("/api/nurse")
    def api_nurse():
        with backend.lock:
            return public(backend.nurse_json())

    @app.get("/api/panel")
    def api_panel():
        with backend.lock:
            return public(backend.panel_json())

    @app.post("/api/nurse/{path:path}")
    async def api_nurse_post(path: str, request: Request):
        body = await _json(request)
        # in a worker thread: "suggest" and "summary" may wait on the AI for seconds, and the
        # patient screen's polls must keep being answered meanwhile
        return public(await run_in_threadpool(safe, lambda: backend.nurse_action(path, body)))

    @app.post("/api/demo")
    async def api_demo(request: Request):
        body = await _json(request)
        return public(await run_in_threadpool(safe, lambda: backend.demo_action(body)))

    @app.post("/api/gaze")
    async def api_gaze(request: Request):
        """Browser eye tracking (web/webgaze.js, /patient?gaze=web): {"spot": "left"|"middle"|"right"|null}."""
        body = await _json(request)
        if not hasattr(backend, "gaze_action"):
            return {"ok": False, "error": "this backend has no browser gaze"}
        return public(safe(lambda: backend.gaze_action(body)))

    return app


async def _json(request):
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


if __name__ == "__main__":
    for n in PAGES:
        path, lea = find_page(n)
        print(f"/{n:<8} -> {os.path.relpath(path, ROOT) if path else 'MISSING'}  ({'Lea' if lea else 'fallback'})")
