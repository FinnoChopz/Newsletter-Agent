from __future__ import annotations

import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

from app.gmail_reader import SCOPES, classify_sender_with_model, discover_newsletters
from app.newsletter_discovery import discover_recommendations, recommendation_to_source
from app.profiles import (
    create_profile,
    list_profiles,
    load_profile,
    profile_paths,
    read_json,
    read_sources,
    set_source_enabled,
    update_schedule,
    upsert_source,
    write_json,
)
from app.scheduler import install_launch_agent, launch_agent_path
from app.signal_runner import run_signal_for_profile


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PROJECT_ROOT / "web"
oauth_sessions: dict[str, dict[str, Any]] = {}


def clean_profile_id(value: str) -> str:
    return unquote(value).strip()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, indent=2).encode("utf-8")


def console_port() -> int:
    return int(os.getenv("PORT", "8787"))


def console_host() -> str:
    return os.getenv("FINN_SIGNAL_CONSOLE_HOST", "127.0.0.1")


def public_base_url(port: int) -> str:
    configured = os.getenv("FINN_SIGNAL_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://localhost:{port}"


def build_oauth_flow(port: int, state: str | None = None) -> Flow:
    base_url = public_base_url(port)
    if base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1"):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    flow = Flow.from_client_secrets_file(
        str(PROJECT_ROOT / "credentials.json"),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = f"{base_url}/oauth2callback"
    return flow


def classify_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classified = []
    for candidate in candidates:
        try:
            result = classify_sender_with_model(candidate)
            classified.append(
                {
                    **candidate,
                    "name": result["suggested_name"],
                    "classification": result["classification"],
                    "confidence": result["confidence"],
                    "reason": result["reason"],
                    "should_include": result["should_include"],
                }
            )
        except Exception as error:
            classified.append(
                {
                    **candidate,
                    "name": candidate["sender"],
                    "classification": "unclear",
                    "confidence": 0,
                    "reason": str(error),
                    "should_include": False,
                }
            )
    return classified


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "FinnSignalConsole/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                self.send_json(
                    {
                        "profiles": list_profiles(),
                        "scheduler": {
                            "installed": launch_agent_path().exists(),
                            "path": str(launch_agent_path()),
                        },
                    }
                )
                return

            if parsed.path == "/api/profiles":
                self.send_json({"profiles": list_profiles()})
                return

            if parsed.path.startswith("/api/profiles/") and parsed.path.endswith("/sources"):
                profile_id = clean_profile_id(parsed.path.split("/")[3])
                self.send_json({"sources": read_sources(profile_id)})
                return

            if parsed.path == "/oauth2callback":
                self.handle_oauth_callback(parsed)
                return

            self.serve_static(parsed.path)
        except Exception as error:
            traceback.print_exc()
            self.send_error_json(500, str(error))

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            body = self.read_body()

            if parsed.path == "/api/profiles":
                profile = create_profile(
                    display_name=str(body.get("display_name", "")),
                    email=str(body.get("email", "")),
                )
                self.send_json({"profile": profile}, status=201)
                return

            if parsed.path == "/api/scheduler/install":
                self.send_json({"scheduler": install_launch_agent(PROJECT_ROOT)})
                return

            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "profiles":
                profile_id = clean_profile_id(parts[2])
                action = parts[3:] if len(parts) > 3 else []
                self.handle_profile_post(profile_id, action, body)
                return

            self.send_error_json(404, "Unknown endpoint.")
        except Exception as error:
            traceback.print_exc()
            self.send_error_json(500, str(error))

    def handle_profile_post(
        self,
        profile_id: str,
        action: list[str],
        body: dict[str, Any],
    ) -> None:
        load_profile(profile_id)

        if action == ["oauth", "start"]:
            flow = build_oauth_flow(self.port)
            auth_url, state = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
            oauth_sessions[state] = {
                "profile_id": profile_id,
                "flow": flow,
            }
            self.send_json({"auth_url": auth_url})
            return

        if action == ["scan"]:
            paths = profile_paths(profile_id)
            candidates = discover_newsletters(
                days=int(body.get("days", 30)),
                max_results=int(body.get("max_results", 300)),
                token_path=paths.token,
            )
            classified = classify_candidates(candidates)
            write_json(paths.candidates, classified)
            self.send_json({"candidates": classified})
            return

        if action == ["sources"]:
            sources = upsert_source(profile_id, body)
            self.send_json({"sources": sources})
            return

        if action == ["sources", "toggle"]:
            sources = set_source_enabled(
                profile_id,
                sender=str(body.get("sender", "")),
                enabled=bool(body.get("enabled", True)),
            )
            self.send_json({"sources": sources})
            return

        if action == ["sources", "import-candidates"]:
            selected = body.get("candidates", [])
            for candidate in selected:
                upsert_source(
                    profile_id,
                    {
                        "name": candidate.get("name") or candidate.get("sender"),
                        "senders": [candidate.get("sender", "")],
                        "enabled": True,
                        "source_type": "gmail_scan",
                        "status": "receiving",
                        "reason": candidate.get("reason", ""),
                    },
                )
            self.send_json({"sources": read_sources(profile_id)})
            return

        if action == ["discover"]:
            profile = load_profile(profile_id)
            user_context = f"{profile.get('display_name')} <{profile.get('email')}>"
            recommendations = discover_recommendations(
                query=str(body.get("query", "")),
                user_context=user_context,
                limit=int(body.get("limit", 6)),
            )
            write_json(profile_paths(profile_id).discovery, recommendations)
            if body.get("auto_add", True):
                for recommendation in recommendations:
                    source = recommendation_to_source(recommendation)
                    if source.get("senders"):
                        upsert_source(profile_id, source)
            self.send_json(
                {
                    "recommendations": recommendations,
                    "sources": read_sources(profile_id),
                }
            )
            return

        if action == ["recommendations", "add"]:
            source = recommendation_to_source(body.get("recommendation", body))
            sources = upsert_source(profile_id, source)
            self.send_json({"sources": sources})
            return

        if action == ["schedule"]:
            profile = update_schedule(
                profile_id,
                time=str(body.get("time", "11:00")),
                frequency=str(body.get("frequency", "daily")),
                enabled=bool(body.get("enabled", True)),
            )
            self.send_json({"profile": profile})
            return

        if action == ["send-test"]:
            self.send_json({"result": run_signal_for_profile(profile_id)})
            return

        self.send_error_json(404, "Unknown profile action.")

    def handle_oauth_callback(self, parsed) -> None:
        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        session = oauth_sessions.pop(state, None)
        if not session:
            self.send_html("<h1>Finn-Signal</h1><p>OAuth session expired. Start again.</p>", 400)
            return

        profile_id = session["profile_id"]
        flow = session["flow"]
        callback_url = f"{public_base_url(self.port)}{self.path}"
        flow.fetch_token(authorization_response=callback_url)

        paths = profile_paths(profile_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.token.write_text(flow.credentials.to_json(), encoding="utf-8")

        self.send_html(
            """
<!doctype html>
<html>
  <head><title>Finn-Signal connected</title></head>
  <body style="font-family:Arial,sans-serif;margin:40px;color:#111827;">
    <h1>Gmail connected</h1>
    <p>You can close this tab and return to Finn-Signal.</p>
  </body>
</html>
""".strip()
        )

    def serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            file_path = WEB_ROOT / "index.html"
        else:
            safe_path = request_path.lstrip("/")
            file_path = WEB_ROOT / safe_path

        try:
            file_path.resolve().relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error_json(403, "Forbidden.")
            return

        if not file_path.exists() or not file_path.is_file():
            self.send_error_json(404, "Not found.")
            return

        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = console_port()
    host = console_host()
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    print(f"Finn-Signal console running at {public_base_url(port)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
