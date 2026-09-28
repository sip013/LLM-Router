import ipaddress
import json
import secrets
import socket
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from llm_router.execute import run_turn
from llm_router.messages import conversation_messages
from llm_router.pipeline import NoEligibleModel
from llm_router.registry import BY_ID, CATALOG
from llm_router.render import render_markdown
from llm_router.trace import TRACE_PATH, label_last

HOST = "0.0.0.0"
PORT = 8765
MESSAGE_CAP = 100_000
IMAGE_CAP = 8 * 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
UPLOAD_ROOT = Path("data/uploads")
PUBLIC_ERRORS = {
    "rate_limited",
    "latency_budget_exceeded",
    "all_models_failed",
    "no_eligible_model",
    "model_call_failed",
}
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)

_lock = threading.Lock()
_sessions: dict[str, dict] = {}


def sniff_image(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    for magic, mime, ext in SIGNATURES:
        if data.startswith(magic):
            return mime, ext
    return None


def _host_name(host: str | None) -> str:
    if not host:
        return ""
    value = host.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 1 else ""
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value


def host_allowed(host: str | None) -> bool:
    name = _host_name(host)
    if not name:
        return False
    if name in {"localhost", socket.gethostname().lower()}:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def share_urls() -> list[str]:
    urls = [f"http://127.0.0.1:{PORT}"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
        if address and not address.startswith("127."):
            urls.append(f"http://{address}:{PORT}")
    except OSError:
        pass
    return urls


def _session(token: str) -> dict:
    with _lock:
        if token not in _sessions:
            _sessions[token] = {
                "history": [],
                "turns": [],
                "last_image": "",
                "last_model": None,
                "verify": False,
                "csrf": "",
            }
        return _sessions[token]


def _cookies(header: str | None) -> dict[str, str]:
    jar = SimpleCookie()
    if header:
        jar.load(header)
    return {key: morsel.value for key, morsel in jar.items()}


def _model_fields(model_id: str) -> dict:
    model = BY_ID.get(model_id)
    return {
        "model_id": model_id,
        "name": (model.display_name if model else "") or model_id,
        "index": CATALOG.index(model) if model else -1,
    }


def _rejected_view(rejected: list[dict]) -> list[dict]:
    return [
        {**_model_fields(row["model_id"]), "reason": row.get("reason"), "detail": row.get("detail")}
        for row in rejected
    ]


def _decision_view(result: dict, image_attached: bool) -> dict:
    decision = result["decision"]
    task = decision.get("task_type")
    jev = decision.get("jev") or {}
    confidence = jev.get("route_confidence")
    if confidence is None:
        confidence = jev.get("task_confidence")
    selected = _model_fields(result["model_id"])
    candidates = []
    for row in decision.get("ranked") or []:
        model = BY_ID[row["model_id"]]
        candidates.append({
            **_model_fields(model.model_id),
            "score": row["score"],
            "quality": model.quality.get(task, 0.0),
            "latency_ms": model.expected_latency_ms,
            "selected": model.model_id == result["model_id"],
        })
    attempts = [
        {
            **_model_fields(row["model_id"]),
            "trigger": row.get("trigger"),
            "error": None if row.get("error") is None else (row["error"] if row["error"] in PUBLIC_ERRORS else "call_failed"),
        }
        for row in result.get("attempts") or []
    ]
    return {
        "html": render_markdown(result["text"]),
        "model_id": selected["model_id"],
        "model_name": selected["name"],
        "model_index": selected["index"],
        "task": task,
        "confidence": confidence,
        "analysis_source": decision.get("analysis_source"),
        "candidates": candidates,
        "rejected": _rejected_view(decision.get("rejected") or []),
        "catalog_size": len(CATALOG),
        "attempts": attempts,
        "elapsed_ms": result.get("elapsed_ms"),
        "validation": result.get("validation"),
        "verification": result.get("verification"),
        "image_attached": image_attached,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send(self, status: int, body: bytes, content_type: str, extra: list[str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for header in extra or []:
            name, value = header.split(": ", 1)
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict, extra: list[str] | None = None):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _reject_host(self) -> bool:
        if host_allowed(self.headers.get("Host")):
            return False
        self._json(400, {"error": "invalid_host"})
        return True

    def _identity(self) -> tuple[str, str, dict] | None:
        cookies = _cookies(self.headers.get("Cookie"))
        session_id = cookies.get("router_session")
        csrf = cookies.get("router_csrf")
        if not session_id or not csrf:
            self._json(403, {"error": "forbidden"})
            return None
        if self.headers.get("X-CSRF-Token") != csrf:
            self._json(403, {"error": "forbidden"})
            return None
        return session_id, csrf, _session(session_id)

    def do_GET(self):
        if self._reject_host():
            return
        path = urlparse(self.path).path
        if path == "/":
            cookies = _cookies(self.headers.get("Cookie"))
            existing = _sessions.get(cookies.get("router_session", ""))
            if existing and existing.get("csrf") == cookies.get("router_csrf"):
                session_id = cookies["router_session"]
                csrf = existing["csrf"]
                extra = []
            else:
                session_id = secrets.token_urlsafe(24)
                csrf = secrets.token_urlsafe(24)
                created = _session(session_id)
                created["csrf"] = csrf
                extra = [
                    f"Set-Cookie: router_session={session_id}; Path=/; SameSite=Strict; HttpOnly",
                    f"Set-Cookie: router_csrf={csrf}; Path=/; SameSite=Strict; HttpOnly",
                ]
            page = (WEB_ROOT / "index.html").read_text(encoding="utf-8").replace("{{CSRF}}", csrf)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", extra)
            return
        if path == "/state":
            cookies = _cookies(self.headers.get("Cookie"))
            state = _sessions.get(cookies.get("router_session", ""))
            if state is None:
                self._json(403, {"error": "forbidden"})
                return
            self._json(200, {"turns": state["turns"], "has_image": bool(state["last_image"]), "verify": state["verify"]})
            return
        files = {
            "/styles.css": "text/css; charset=utf-8",
            "/app.js": "text/javascript; charset=utf-8",
            "/scene.js": "text/javascript; charset=utf-8",
            "/vendor/three.module.min.js": "text/javascript; charset=utf-8",
            "/favicon.svg": "image/svg+xml",
            "/fonts/inter.woff2": "font/woff2",
            "/fonts/newsreader.woff2": "font/woff2",
            "/fonts/newsreader-italic.woff2": "font/woff2",
            "/fonts/jetbrains-mono.woff2": "font/woff2",
        }
        if path in files:
            file_path = (WEB_ROOT / path.lstrip("/")).resolve()
            if not str(file_path).startswith(str(WEB_ROOT.resolve())):
                self._json(404, {"error": "not_found"})
                return
            self._send(200, file_path.read_bytes(), files[path])
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if self._reject_host():
            return
        identity = self._identity()
        if identity is None:
            return
        _session_id, _csrf, state = identity
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length > IMAGE_CAP + 2_000_000:
            self._json(413, {"error": "payload_too_large"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "invalid_json"})
            return
        if path == "/reset":
            state["history"].clear()
            state["turns"].clear()
            state["last_image"] = ""
            self._json(200, {"ok": True})
            return
        if path == "/label":
            label = body.get("label")
            if label not in {"pass", "fail"} or not label_last(label):
                self._json(400, {"error": "not_labeled"})
                return
            self._json(200, {"ok": True})
            return
        if path != "/turn":
            self._json(404, {"error": "not_found"})
            return
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            self._json(400, {"error": "empty_message"})
            return
        if len(message) > MESSAGE_CAP:
            self._json(413, {"error": "message_too_long"})
            return
        verify = body.get("verify") is True
        state["verify"] = verify
        image_path = ""
        image_attached = False
        if body.get("reuse_image") is True and state["last_image"]:
            image_path = state["last_image"]
            image_attached = True
        elif body.get("image_base64"):
            saved = _save_image(body.get("image_base64"), body.get("image_type"))
            if isinstance(saved, str) and saved.startswith("error:"):
                self._json(400, {"error": saved.removeprefix("error:")})
                return
            image_path = saved
            image_attached = True
        state["history"].append({"role": "user", "text": message.strip()})
        try:
            result = run_turn(
                state["history"],
                image_path,
                state["last_model"],
                conversation_messages,
                require_verification=verify,
                trace_path=TRACE_PATH,
            )
        except NoEligibleModel as exc:
            state["history"].pop()
            self._json(400, {"error": "no_eligible_model", "rejected": _rejected_view(exc.rejected)})
            return
        except Exception as exc:
            state["history"].pop()
            code = str(exc)
            self._json(502, {"error": code if code in PUBLIC_ERRORS else "model_call_failed"})
            return
        view = _decision_view(result, image_attached)
        state["history"].append({"role": "assistant", "text": result["text"]})
        state["last_model"] = result["model_id"]
        if image_path:
            state["last_image"] = image_path
        state["turns"].append({"role": "user", "text": message.strip(), "image_attached": image_attached})
        state["turns"].append({"role": "assistant", **view})
        self._json(200, view)


def _save_image(encoded, declared) -> str:
    import base64
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return "error:invalid_image"
    if len(data) > IMAGE_CAP:
        return "error:image_too_large"
    sniffed = sniff_image(data)
    if sniffed is None or declared != sniffed[0]:
        return "error:invalid_image"
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_ROOT / f"{secrets.token_hex(16)}{sniffed[1]}"
    path.write_bytes(data)
    return str(path)


def serve() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    for url in share_urls():
        print(f"Open {url}", flush=True)
    server.serve_forever()
