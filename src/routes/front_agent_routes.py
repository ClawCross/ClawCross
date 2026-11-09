"""Frontend proxy for the unified Agent catalog and control plane."""

from flask import jsonify, request, session
import requests


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
