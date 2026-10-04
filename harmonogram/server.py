"""Lokální webový server: statické soubory + JSON API."""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .calendar_cz import CzechHolidayCalendar
from .docx_export import DocxHarmonogramExporter, VariantResult
from .model import PlanStateMapper
from .parliament import PageDownloader, ParliamentCalendarStore
from .variants import VariantStore
from .process import DefaultProcessFactory
from .scheduler import ScheduleCalculator, ScheduleSerializer


def package_dir() -> Path:
    """Složka balíčku harmonogram (funguje i v balíčku z PyInstalleru)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "harmonogram"


def resource_dir() -> Path:
    return package_dir() / "static"


def template_path() -> Path:
    return package_dir() / "templates" / "harmonogram_sablona.docx"


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class HarmonogramService:
    """Aplikační logika volaná z HTTP vrstvy."""

    def __init__(self, parliament: ParliamentCalendarStore | None = None) -> None:
        self.calendar = CzechHolidayCalendar()
        self.factory = DefaultProcessFactory(self.calendar)
        self.serializer = ScheduleSerializer(self.calendar)
        self.parliament = parliament or ParliamentCalendarStore()
        self.variants = VariantStore()

    def _calculate(self, state: dict[str, Any] | None):
        plan = PlanStateMapper.apply_state(self.factory.build(), state)
        result = ScheduleCalculator(plan, self.calendar, self.parliament.calendars).calculate()
        return plan, result

    def refresh_parliament(self, state: dict[str, Any] | None) -> dict[str, Any]:
        self.parliament.refresh()
        return self.compute(state)

    def import_parliament(self, data: dict[str, Any]) -> dict[str, Any]:
        chamber = data.get("chamber")
        if chamber not in ("psp", "senat"):
            raise ValueError("Neznámá komora.")
        if data.get("html_b64"):
            page = PageDownloader.decode(base64.b64decode(data["html_b64"]))
        else:
            page = str(data.get("html") or "")
        count = self.parliament.import_html(chamber, page, data.get("name") or "soubor")
        payload = self.compute(data.get("state"))
        payload["imported"] = count
        return payload

    def export_docx(self, payload: dict[str, Any] | None) -> bytes:
        """Buď jeden stav, nebo {"variants": [{"name", "state"}], "primary": index}."""
        exporter = DocxHarmonogramExporter(template_path())
        items = (payload or {}).get("variants") if isinstance(payload, dict) else None
        if not items:
            _, result = self._calculate(payload)
            return exporter.export(result)
        variants = [VariantResult(str(v.get("name") or ""), self._calculate(v.get("state"))[1])
                    for v in items if isinstance(v, dict)]
        primary = payload.get("primary") if isinstance(payload.get("primary"), int) else 0
        return exporter.export_variants(variants, min(max(primary, 0), len(variants) - 1))

    def compute(self, state: dict[str, Any] | None) -> dict[str, Any]:
        plan, result = self._calculate(state)
        payload = self.serializer.to_dict(result)
        payload["state"] = PlanStateMapper.to_state(plan)
        payload["parliament"]["refreshing"] = self.parliament.refreshing
        return payload


class HarmonogramRequestHandler(BaseHTTPRequestHandler):
    service: HarmonogramService      # nastavuje HarmonogramServer
    static_root: Path
    server_version = "Harmonogram/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/default":
            self._send_json(self.service.compute(None))
        elif path == "/api/variants":
            self._send_json(self.service.variants.load())
        elif path in ("/", ""):
            self._send_file(self.static_root / "index.html")
        else:
            target = (self.static_root / path.lstrip("/")).resolve()
            if self.static_root.resolve() not in target.parents:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(target)

    def do_POST(self) -> None:  # noqa: N802
        routes = {
            "/api/compute": lambda d: self._send_json(self.service.compute(d)),
            "/api/variants": lambda d: self._send_json(self.service.variants.save(d)),
            "/api/parliament/refresh": lambda d: self._send_json(self.service.refresh_parliament(d)),
            "/api/parliament/import": lambda d: self._send_json(self.service.import_parliament(d)),
            "/api/export/docx": lambda d: self._send_bytes(self.service.export_docx(d), DOCX_MIME, "harmonogram.docx"),
        }
        route = routes.get(self.path)
        if route is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            route(json.loads(self.rfile.read(length) or b"{}"))
        except (ValueError, KeyError, TypeError, OSError, binascii.Error) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _send_bytes(self, body: bytes, ctype: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # tichý provoz
        pass


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    # Na Windows by SO_REUSEADDR dovolil obsadit už používaný port.
    allow_reuse_address = False
    daemon_threads = True


class HarmonogramServer:
    """Spouští HTTP server na pozadí na volném portu."""

    DEFAULT_PORT = 8421
    AVOID_PORTS = {8765}

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, auto_refresh: bool = True):
        self.auto_refresh = auto_refresh
        self.host = host
        self.preferred_port = port
        self.port: int | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> str:
        service = HarmonogramService()
        if self.auto_refresh and service.parliament.is_stale():
            service.parliament.refresh_async()   # na pozadí, UI zobrazí stav
        handler = type("Handler", (HarmonogramRequestHandler,), {
            "service": service,
            "static_root": resource_dir(),
        })
        for port in self._candidate_ports():
            try:
                self._httpd = _ExclusiveHTTPServer((self.host, port), handler)
            except OSError:
                continue
            self.port = self._httpd.server_address[1]
            break
        else:
            raise RuntimeError("Nepodařilo se najít volný port.")
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _candidate_ports(self):
        for port in range(self.preferred_port, self.preferred_port + 30):
            if port not in self.AVOID_PORTS:
                yield port
        yield 0  # nechat vybrat systém
