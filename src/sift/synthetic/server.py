"""A small static server for the synthetic web (the Docker `synthetic-web` service and `sift synthetic-server`).

Routes by Host header (`curl -H 'Host: alpine-outfitters.example' localhost:8090/catalog/`), which is how the worker
reaches it, or by path prefix (`http://localhost:8090/alpine-outfitters.example/catalog/`) for a person browsing the
fictional sites; in prefix mode root-relative links in HTML are rewritten to stay under the prefix. The layout (v1 or
v2) comes from the X-Synthetic-Layout header or the server's default."""

from __future__ import annotations

import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from sift.crawl.fetch import DiskSource


def make_handler(root: Path, default_layout: str) -> type[BaseHTTPRequestHandler]:
    sources = {layout: DiskSource(root, layout) for layout in ("v1", "v2")}

    class Handler(BaseHTTPRequestHandler):
        server_version = "SiftSyntheticWeb/0.1"

        def log_message(self, format: str, *args: object) -> None:  # quiet by default
            return

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            layout = self.headers.get("X-Synthetic-Layout") or default_layout
            source = sources.get(layout, sources["v1"])
            path, prefix = parts.path or "/", ""
            if not host.endswith(".example"):
                match = re.match(r"^/([a-z0-9.-]+\.example)(/.*)?$", path)
                if not match:
                    body = (
                        (root / "index.html").read_bytes()
                        if (root / "index.html").exists()
                        else b"Run `sift seed` first."
                    )
                    self._send(200, "text/html; charset=utf-8", body)
                    return
                host, path, prefix = match.group(1), match.group(2) or "/", f"/{match.group(1)}"
            status, ctype, body = source.get(host, path, parts.query)
            if prefix and ctype.startswith("text/html"):
                body = re.sub(rb'(href|src|data-src)="/(?!/)', rb'\1="' + prefix.encode() + b"/", body)
            self._send(status, ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""), body)

        def _send(self, status: int, ctype: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(root: Path, *, host: str = "0.0.0.0", port: int = 8090, layout: str = "v1") -> None:
    server = ThreadingHTTPServer((host, port), make_handler(root, layout))
    try:
        server.serve_forever()
    finally:
        server.server_close()
