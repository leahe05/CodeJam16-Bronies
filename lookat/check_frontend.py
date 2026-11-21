"""Checks Lea's pages in web/ before they are merged, so switching to them just works.

Run:  py -3.11 -m lookat.check_frontend      (exit code 0 = OK)

Checks:
  - each page (patient, nurse, panel) is found where lookat/server.py looks
  - the pages call the JSON API (/api/screen, /api/nurse, /api/panel) with RELATIVE urls
  - no hard-coded localhost / IP addresses (the nurse phone could not reach them)
  - no files loaded from the internet (CDN, Google Fonts): the hotspot may have no internet
  - a build-tool project (package.json) has its built output in web/dist
"""
import os
import re
import sys

from lookat.server import API_FOR_PAGE, PAGES, WEB_DIR, find_page

TEXT_EXT = (".html", ".htm", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".css", ".vue", ".svelte")
SKIP_DIRS = {"node_modules", ".git", "__pycache__"}
HARDCODED = re.compile(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\d+\.\d+\.\d+\.\d+)(:\d+)?", re.I)
EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//[^"']+|@import\s+url\(\s*["']?https?://|"""
                      r"""from\s+["']https?://""", re.I)


def _text_files(web_dir):
    for root, dirs, files in os.walk(web_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.lower().endswith(TEXT_EXT):
                yield os.path.join(root, f)


def check(web_dir: str = WEB_DIR) -> list:
    """Return a list of (level, message); level is "ok", "warn" or "error"."""
    out = []
    if not os.path.isdir(web_dir):
        return [("error", f"{web_dir} does not exist")]
    sources = {}
    for path in _text_files(web_dir):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                sources[path] = f.read()
        except OSError:
            continue
    everything = "\n".join(sources.values())

    for name in PAGES:
        path, lea = find_page(name, web_dir)
        if lea:
            out.append(("ok", f"/{name} -> {os.path.relpath(path, web_dir)}"))
        else:
            out.append(("warn", f"/{name}: no page yet (the team test page is used)"))
        api = API_FOR_PAGE[name]
        if lea and api not in everything:
            out.append(("error", f"/{name}: nothing calls {api}, so the page would show no live data (docs/API.md)"))

    for path, text in sources.items():
        rel = os.path.relpath(path, web_dir)
        if os.sep + "dist" + os.sep in os.sep + rel and rel.endswith(".js"):
            continue  # minified build output: checked through the source files
        for m in HARDCODED.finditer(text):
            out.append(("error", f"{rel}: hard-coded address {m.group(0)}; use relative URLs like fetch('/api/screen')"))
        for m in EXTERNAL.finditer(text):
            out.append(("warn", f"{rel}: loads from the internet ({m.group(0)[:60]}); may fail on the hotspot"))

    if os.path.isfile(os.path.join(web_dir, "package.json")) and not os.path.isdir(os.path.join(web_dir, "dist")):
        out.append(("error", "web/package.json found but no web/dist: run the build and commit web/dist"))
    return out


def main() -> int:
    results = check()
    for level, msg in results:
        print(f"{level.upper():5}  {msg}")
    errors = sum(1 for level, _ in results if level == "error")
    print("PASS" if not errors else f"FAIL ({errors} error(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
