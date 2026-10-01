"""Frontend relay for the agent and team APIs of the Agent service.

``/v1/agents/...`` and ``/v1/teams/...`` are forwarded as they are. A browser
session is forwarded as its user; without one, the client's own
``Authorization: Bearer <user>:<password>`` is, so any frontend can use them.
"""

from urllib.parse import quote

from flask import Response, jsonify, request, session
import requests

# Reachable without a browser session: the Agent service authenticates the
# forwarded ``Authorization`` itself, like /v1/chat/completions.
PUBLIC_AGENT_ENDPOINTS = frozenset({"public_agents", "public_teams"})

_METHODS = ["GET", "POST", "PATCH", "DELETE"]


def register_agent_routes(app, *, port_agent: int, internal_token: str) -> None:
    base_url = f"http://127.0.0.1:{port_agent}"

    def _auth_headers():
        user_id = str(session.get("user_id") or "").strip()
        if user_id:
            return {"Authorization": f"Bearer {internal_token}:{user_id}"}
        auth = request.headers.get("Authorization", "")
        return {"Authorization": auth} if auth else {}

    def _relay(path: str):
        # Asking an agent may take as long as the agent does.
        body = request.get_json(silent=True) if request.method in ("POST", "PATCH") else None
        long_control = path.endswith("/control") and isinstance(body, dict) and body.get("action") == "compact"
        timeout = 900 if path.endswith("/messages") or long_control else 60
        try:
            response = requests.request(
                request.method,
                f"{base_url}{path}",
                params=request.args,
                json=body,
                headers=_auth_headers(),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502
        return Response(
            response.content,
            status=response.status_code,
            content_type=response.headers.get("content-type", "application/json"),
        )

    @app.route("/v1/agents", methods=_METHODS, endpoint="public_agents")
    @app.route("/v1/agents/<path:rest>", methods=_METHODS, endpoint="public_agents")
    def public_agents(rest: str = ""):
        return _relay("/v1/agents" + (f"/{quote(rest, safe='/')}" if rest else ""))

    @app.route("/v1/teams", methods=_METHODS, endpoint="public_teams")
    @app.route("/v1/teams/<path:rest>", methods=_METHODS, endpoint="public_teams")
    def public_teams(rest: str = ""):
        return _relay("/v1/teams" + (f"/{quote(rest, safe='/')}" if rest else ""))
