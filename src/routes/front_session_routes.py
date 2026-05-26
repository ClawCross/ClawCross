"""
Flask 前端会话代理路由模块

为 Flask 前端提供会话相关的代理路由：
- /proxy_sessions：代理会话列表
- /proxy_sessions_status：代理会话状态
- /proxy_session_history：代理会话历史
- /proxy_session/delete：代理删除会话
"""

from flask import jsonify, request, session
import requests


def register_session_routes(
    app,
    *,
    port_agent: int,
    internal_token: str,
    local_sessions_url: str,
    local_session_history_url: str,
    local_session_status_url: str,
    local_delete_session_url: str,
) -> None:
    """Register session-related proxy routes for Flask frontend."""

    def _internal_auth_headers():
        return {"X-Internal-Token": internal_token}

    @app.route("/proxy_sessions")
    def proxy_sessions():
        user_id = session.get("user_id", "")
        try:
            r = requests.post(
                local_sessions_url,
                json={"user_id": user_id},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_sessions_status")
    def proxy_sessions_status():
        user_id = session.get("user_id", "")
        try:
            r = requests.post(
                "http://127.0.0.1:{port}/sessions_status".format(port=port_agent),
                json={"user_id": user_id},
                headers=_internal_auth_headers(),
                timeout=5,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_session_history", methods=["POST"])
    def proxy_session_history():
        user_id = session.get("user_id", "")
        sid = request.json.get("session_id", "")
        try:
            r = requests.post(
                local_session_history_url,
                json={"user_id": user_id, "session_id": sid},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_session_status", methods=["POST"])
    def proxy_session_status():
        user_id = session.get("user_id", "")
        sid = request.json.get("session_id", "") if request.is_json else ""
        try:
            r = requests.post(
                local_session_status_url,
                json={"user_id": user_id, "session_id": sid},
                headers=_internal_auth_headers(),
                timeout=5,
            )
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({
                "has_new_messages": False,
                "pending_count": 0,
                "busy": False,
                "busy_source": "",
                "context_percent": 0,
                "context_remaining": 0,
                "context_tokens": 0,
                "context_budget": 0,
            }), 200

    @app.route("/proxy_delete_session", methods=["POST"])
    def proxy_delete_session():
        user_id = session.get("user_id", "")
        sid = request.json.get("session_id", "") if request.is_json else ""
        try:
            r = requests.post(
                local_delete_session_url,
                json={"user_id": user_id, "session_id": sid},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500
