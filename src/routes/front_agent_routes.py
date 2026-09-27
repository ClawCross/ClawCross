"""Frontend proxy for the unified Agent catalog and control plane."""

from urllib.parse import quote

from flask import Response, jsonify, request, session
import requests

# Reachable without a browser session: the Agent service authenticates the
# forwarded ``Authorization`` itself, like /v1/chat/completions.
PUBLIC_AGENT_ENDPOINTS = frozenset({
    "public_agents_list",
    "public_agent_message",
    "public_agent_control",
    "public_agent_card",
})


_ACTIONS = {"list", "status", "cancel", "stop", "new", "reset", "delete", "configure"}
_KINDS = {"", "internal", "external", "subagent"}


def register_agent_routes(
    app,
    *,
    port_agent: int,
    internal_token: str,
) -> None:
    base_url = f"http://127.0.0.1:{port_agent}"

    def _internal_headers():
        return {"X-Internal-Token": internal_token}

    def _public_auth_headers():
        """Browser session → internal bearer; otherwise the client's own ``Bearer user:password``."""
        user_id = str(session.get("user_id") or "").strip()
        if user_id:
            return {"Authorization": f"Bearer {internal_token}:{user_id}"}
        auth = request.headers.get("Authorization", "")
        return {"Authorization": auth} if auth else {}

    def _relay(response):
        return Response(
            response.content,
            status=response.status_code,
            content_type=response.headers.get("content-type", "application/json"),
        )

    # ── L1 agent layer: one public interface to every agent on this machine ──

    @app.route("/v1/agents", methods=["GET"])
    def public_agents_list():
        try:
            return _relay(requests.get(f"{base_url}/v1/agents", headers=_public_auth_headers(), timeout=20))
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/v1/agents/<path:ref>/messages", methods=["POST"])
    def public_agent_message(ref):
        try:
            return _relay(requests.post(
                f"{base_url}/v1/agents/{quote(ref, safe='/')}/messages",
                json=request.get_json(silent=True) or {},
                headers=_public_auth_headers(),
                timeout=900,
            ))
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/v1/agents/<path:ref>/control", methods=["POST"])
    def public_agent_control(ref):
        try:
            return _relay(requests.post(
                f"{base_url}/v1/agents/{quote(ref, safe='/')}/control",
                json=request.get_json(silent=True) or {},
                headers=_public_auth_headers(),
                timeout=60,
            ))
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/v1/agents/<path:ref>", methods=["GET"])
    def public_agent_card(ref):
        try:
            return _relay(requests.get(
                f"{base_url}/v1/agents/{quote(ref, safe='/')}",
                headers=_public_auth_headers(),
                timeout=20,
            ))
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502

    @app.route("/proxy_agent_control", methods=["POST"])
    def proxy_agent_control():
        user_id = str(session.get("user_id") or "").strip()
        if not user_id:
            return jsonify({"error": "login required"}), 401

        body = request.get_json(silent=True) if request.is_json else {}
        body = body if isinstance(body, dict) else {}
        action = str(body.get("action") or "list").strip().lower()
        kind = str(body.get("kind") or "").strip().lower()
        if action not in _ACTIONS:
            return jsonify({"error": f"unsupported action: {action}"}), 400
        if kind not in _KINDS:
            return jsonify({"error": f"unsupported kind: {kind}"}), 400

        payload = {
            "user_id": user_id,
            "action": action,
            "kind": kind,
            "identity": str(body.get("identity") or "").strip(),
            "refresh_external": bool(body.get("refresh_external", False)),
        }
        if action == "configure":
            settings = body.get("settings")
            if not isinstance(settings, dict):
                return jsonify({"error": "settings must be an object"}), 400
            payload["settings"] = settings
        team = str(body.get("team") or "").strip()
        if team:
            payload["team"] = team
        timeout = 60 if payload["refresh_external"] else 20
        try:
            response = requests.post(
                f"{base_url}/agent_control",
                json=payload,
                headers=_internal_headers(),
                timeout=timeout,
            )
            return jsonify(response.json()), response.status_code
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502
        except ValueError:
            return jsonify({"error": "Agent service returned invalid JSON"}), 502
