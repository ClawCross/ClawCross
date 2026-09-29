"""
Flask 前端群聊代理路由模块

- /proxy_groups/...：原样转发到 Agent 服务的 /groups/...（以当前登录用户身份）
- 外部运行时的会话列表 / 清理、远程 Claude 会话、harness 状态
"""

from urllib.parse import quote

from flask import Response, jsonify, request, session
import requests

from harness.remote_claude_agents import (
    list_remote_claude_sessions,
    read_remote_claude_messages,
    send_remote_claude_message,
)


def _remote_session_keys(item: dict) -> set[str]:
    return {
        str(item.get(key) or "").strip()
        for key in ("display_id", "bridge_session_id", "session_id", "id", "job_id")
        if str(item.get(key) or "").strip()
    }


def _split_remote_user_host(remote_host: str, *, fallback_user: str = "", fallback_host: str = "") -> tuple[str, str]:
    """Normalize remote identity to separate user and host.

    Harness events often send SSH-style targets such as ``user@host.example``
    while the live remote payload already carries ``remote.user`` separately.  If
    both are concatenated again in the frontend, the label becomes
    ``user@user@host.example``.
    """

    raw = str(remote_host or "").strip()
    user = str(fallback_user or "").strip()
    host = str(fallback_host or "").strip()
    if raw and "@" in raw:
        parts = [part for part in raw.split("@") if part]
        if parts:
            host = parts[-1]
        if len(parts) >= 2:
            user = parts[-2]
    elif raw:
        host = raw
    return user, host


def _merge_review_harness_sessions(data: dict, harness_state: dict) -> dict:
    """Keep review-bound harness sessions visible even after remote daemon settles."""

    sessions = data.setdefault("sessions", [])
    if not isinstance(sessions, list):
        data["sessions"] = sessions = []
    live_keys: set[str] = set()
    for item in sessions:
        if isinstance(item, dict):
            live_keys.update(_remote_session_keys(item))

    tasks = {
        str(task.get("task_id") or ""): task
        for task in harness_state.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }
    for agent in harness_state.get("agents", []) or []:
        if not isinstance(agent, dict):
            continue
        session_ref = str(agent.get("session_ref") or "").strip()
        task_id = str(agent.get("current_task_id") or "").strip()
        task = tasks.get(task_id)
        if not session_ref or session_ref in live_keys or not task:
            continue
        if str(task.get("status") or "").lower() != "review":
            continue
        remote_user, remote_host = _split_remote_user_host(
            agent.get("remote_host") or "",
            fallback_user=data.get("remote", {}).get("user") or "",
            fallback_host=data.get("remote", {}).get("host") or "",
        )
        sessions.append(
            {
                "display_id": session_ref,
                "bridge_session_id": session_ref,
                "title": task.get("title") or task_id or agent.get("agent_id") or "Review session",
                "status": "review",
                "cwd": agent.get("worktree") or "",
                "updated_at": agent.get("updated_at") or task.get("updated_at") or "",
                "remote_host": remote_host,
                "remote_user": agent.get("remote_user") or remote_user,
                "harness_review_placeholder": True,
                "agent_id": agent.get("agent_id") or "",
                "current_task_id": task_id,
                "last_message": {
                    "role": "harness",
                    "content": agent.get("message") or "TODO 已完成，等待审查；远端 Claude daemon 已结束该 live session。",
                    "timestamp": agent.get("updated_at") or "",
                },
            }
        )
        live_keys.add(session_ref)
    data["sessions"] = sessions
    return data


def register_group_routes(app, *, port_agent: int, internal_token: str) -> None:
    """Register group-chat proxy routes for Flask frontend."""

    def _group_auth_headers():
        user_id = session.get("user_id", "")
        return {
            "Authorization": "Bearer {token}:{user}".format(token=internal_token, user=user_id),
        }

    @app.route("/proxy_groups", methods=["GET", "POST"], endpoint="proxy_groups")
    @app.route("/proxy_groups/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], endpoint="proxy_groups")
    def proxy_groups(rest: str = ""):
        """Forward group chat calls to the Agent service's /groups as the logged-in user."""
        path = "/groups" + (f"/{quote(rest, safe='/')}" if rest else "")
        try:
            r = requests.request(
                request.method,
                f"http://127.0.0.1:{port_agent}{path}",
                params=request.args,
                json=request.get_json(silent=True) if request.method in ("POST", "PUT", "PATCH") else None,
                headers=_group_auth_headers(),
                timeout=30,
            )
            return Response(r.content, status=r.status_code,
                            content_type=r.headers.get("content-type", "application/json"))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_sessions_list", methods=["POST"])
    def proxy_sessions_list():
        """代理 sessions 列表查询：acpx sessions。"""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"error": "未登录"}), 401
        try:
            r = requests.post(
                "http://127.0.0.1:{port}/sessions_list".format(port=port_agent),
                json={"user_id": user_id},
                headers={"X-Internal-Token": internal_token},
                timeout=30,
            )
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {"error": r.text or "Unknown error"}
            return jsonify(resp_data), r.status_code
        except requests.exceptions.Timeout:
            return jsonify({"error": "查询超时"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_remote_claude_sessions", methods=["GET"])
    def proxy_remote_claude_sessions():
        """Read remote Claude Code background-agent sessions for the mobile UI."""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"ok": False, "error": "未登录"}), 401
        try:
            limit = int(request.args.get("limit", "3") or "3")
        except ValueError:
            limit = 3
        try:
            data = list_remote_claude_sessions(limit=max(1, min(limit, 40)))
            try:
                r = requests.get(
                    "http://127.0.0.1:{port}/harness/state".format(port=port_agent),
                    params={"user_id": user_id},
                    headers={"X-Internal-Token": internal_token},
                    timeout=5,
                )
                if r.ok:
                    data = _merge_review_harness_sessions(data, r.json())
            except Exception:
                pass
            return jsonify(data), 200
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "sessions": []}), 200

    @app.route("/proxy_remote_claude_sessions/<path:session_id>/messages", methods=["GET"])
    def proxy_remote_claude_session_messages(session_id):
        """Read one remote Claude Code transcript by local or bridge session id."""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"ok": False, "error": "未登录"}), 401
        try:
            limit = int(request.args.get("limit", "120") or "120")
        except ValueError:
            limit = 120
        try:
            data = read_remote_claude_messages(session_id, limit=max(1, min(limit, 300)))
            return jsonify(data), 200
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "messages": []}), 200

    @app.route("/proxy_remote_claude_sessions/<path:session_id>/messages", methods=["POST"])
    def proxy_remote_claude_session_send_message(session_id):
        """Send a user reply to one remote Claude Code background-agent session."""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"ok": False, "error": "未登录"}), 401
        body = request.get_json(silent=True) or {}
        text = body.get("text") or body.get("message") or ""
        if not isinstance(text, str):
            return jsonify({"ok": False, "error": "消息必须是文本"}), 400
        if not text.strip():
            return jsonify({"ok": False, "error": "消息不能为空"}), 400
        try:
            data = send_remote_claude_message(session_id, text)
            return jsonify(data), 200
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 200

    @app.route("/proxy_harness_state", methods=["GET"])
    def proxy_harness_state():
        """Read ClawCross harness state for the mobile message center."""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"ok": False, "error": "未登录"}), 401
        try:
            r = requests.get(
                "http://127.0.0.1:{port}/harness/state".format(port=port_agent),
                params={"user_id": user_id},
                headers={"X-Internal-Token": internal_token},
                timeout=15,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "tasks": [], "agents": [], "runs": []}), 500

    @app.route("/proxy_harness_event", methods=["POST"])
    def proxy_harness_event():
        """Post a harness event through the logged-in user's ClawCross session."""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"ok": False, "error": "未登录"}), 401
        body = request.get_json(silent=True) or {}
        body["user_id"] = user_id
        try:
            r = requests.post(
                "http://127.0.0.1:{port}/harness/event".format(port=port_agent),
                json=body,
                headers={"X-Internal-Token": internal_token},
                timeout=15,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500


    @app.route("/proxy_sessions_close", methods=["POST"])
    def proxy_sessions_close():
        """关闭指定的 acpx session。"""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"error": "未登录"}), 401
        try:
            data = request.get_json(silent=True) or {}
            data["user_id"] = user_id
            r = requests.post(
                "http://127.0.0.1:{port}/sessions_close".format(port=port_agent),
                json=data,
                headers={"X-Internal-Token": internal_token},
                timeout=15,
            )
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {"error": r.text or "Unknown error"}
            return jsonify(resp_data), r.status_code
        except requests.exceptions.Timeout:
            return jsonify({"error": "关闭超时"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500
