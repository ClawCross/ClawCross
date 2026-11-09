"""
Flask 前端群聊代理路由模块

为 Flask 前端提供群聊相关的代理路由：
- /proxy_groups：代理群聊列表/创建请求
- /proxy_groups/{group_id}：代理群聊详情
- /proxy_groups/{group_id}/messages：代理消息
"""

from urllib.parse import quote

from flask import jsonify, request, session
import requests

from integrations.remote_claude_agents import (
    list_remote_claude_sessions,
    read_remote_claude_messages,
    send_remote_claude_message,
)


def _enc_group_seg(segment: str) -> str:
    """Path segment for FastAPI /groups/{group_id}/... (Unicode, #, :, etc.)."""
    return quote(segment or "", safe="")

# Agent /acp_control may run acpx OpenClaw exec /new for up to ~180s; a short proxy
# timeout makes the UI show "超时" while the backend still succeeds.
_PROXY_ACP_CONTROL_TIMEOUT_SEC = 240


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

    @app.route("/proxy_groups", methods=["GET"])
    def proxy_list_groups():
        try:
            r = requests.get(
                "http://127.0.0.1:{port}/groups".format(port=port_agent),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups", methods=["POST"])
    def proxy_create_group():
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            body = request.get_json(silent=True) or {}
            print(f"[DEBUG proxy_create_group] body={body}")
            # Validate required fields before forwarding
            if not body.get("name"):
                return jsonify({"error": "缺少必填字段: name"}), 400
            r = requests.post(
                "http://127.0.0.1:{port}/groups".format(port=port_agent),
                json=body,
                headers=headers,
                timeout=15,
            )
            # Ensure we always return valid JSON
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {"error": r.text or "Unknown error"}
            # If backend returned an error, try to extract detail (FastAPI format)
            if r.status_code >= 400:
                detail = resp_data.get("detail") or resp_data.get("error") or str(resp_data)
                return jsonify({"error": detail}), r.status_code
            return jsonify(resp_data), r.status_code
        except requests.exceptions.ConnectionError:
            return jsonify({"error": "无法连接到 Agent 服务，请确认后端已启动"}), 502
        except requests.exceptions.Timeout:
            return jsonify({"error": "Agent 服务响应超时"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>", methods=["GET"])
    def proxy_get_group(group_id):
        print(f"[DEBUG proxy_get_group] group_id={repr(group_id)}")
        try:
            r = requests.get(
                "http://127.0.0.1:{port}/groups/{gid}".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=20,
            )
            print(f"[DEBUG proxy_get_group] agent_status={r.status_code} body={r.text[:200]}")
            return jsonify(r.json()), r.status_code
        except Exception as e:
            print(f"[DEBUG proxy_get_group] exception={e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>", methods=["PUT"])
    def proxy_update_group(group_id):
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            r = requests.put(
                "http://127.0.0.1:{port}/groups/{gid}".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                json=request.get_json(silent=True),
                headers=headers,
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>", methods=["DELETE"])
    def proxy_delete_group(group_id):
        try:
            r = requests.delete(
                "http://127.0.0.1:{port}/groups/{gid}".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/messages", methods=["GET"])
    def proxy_group_messages(group_id):
        try:
            after_id = request.args.get("after_id", "0")
            r = requests.get(
                "http://127.0.0.1:{port}/groups/{gid}/messages".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                params={"after_id": after_id},
                headers=_group_auth_headers(),
                timeout=20,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/messages", methods=["POST"])
    def proxy_post_group_message(group_id):
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/messages".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                json=request.get_json(silent=True),
                headers=headers,
                timeout=30,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/mute", methods=["POST"])
    def proxy_mute_group(group_id):
        try:
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/mute".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/unmute", methods=["POST"])
    def proxy_unmute_group(group_id):
        try:
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/unmute".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/mute_status", methods=["GET"])
    def proxy_group_mute_status(group_id):
        try:
            r = requests.get(
                "http://127.0.0.1:{port}/groups/{gid}/mute_status".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/typing", methods=["GET"])
    def proxy_group_typing(group_id):
        try:
            r = requests.get(
                "http://127.0.0.1:{port}/groups/{gid}/typing".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=5,
            )
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"typing": []}), 200

    @app.route("/proxy_groups/<group_id>/sessions", methods=["GET"])
    def proxy_group_sessions(group_id):
        try:
            r = requests.get(
                "http://127.0.0.1:{port}/groups/{gid}/sessions".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=15,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"sessions": [], "error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/sync_members", methods=["POST"])
    def proxy_sync_members(group_id):
        try:
            team_name = request.args.get("team_name", "")
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/sync_members".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                params={"team_name": team_name} if team_name else {},
                headers=_group_auth_headers(),
                timeout=30,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/members", methods=["POST"])
    def proxy_add_member(group_id):
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/members".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                json=request.get_json(silent=True),
                headers=headers,
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/members/<global_id>", methods=["DELETE"])
    def proxy_remove_member(group_id, global_id):
        try:
            r = requests.delete(
                "http://127.0.0.1:{port}/groups/{gid}/members/{gmid}".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                    gmid=_enc_group_seg(global_id),
                ),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/members/mute", methods=["POST"])
    def proxy_mute_group_member(group_id):
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/members/mute".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                json=request.get_json(silent=True),
                headers=headers,
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/mute_all", methods=["POST"])
    def proxy_mute_all_group_agents(group_id):
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/mute_all".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                json=request.get_json(silent=True),
                headers=headers,
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/primary", methods=["POST"])
    def proxy_set_primary_agent(group_id):
        try:
            headers = _group_auth_headers()
            headers["Content-Type"] = "application/json"
            r = requests.post(
                "http://127.0.0.1:{port}/groups/{gid}/primary".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                json=request.get_json(silent=True),
                headers=headers,
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_groups/<group_id>/primary", methods=["DELETE"])
    def proxy_clear_primary_agent(group_id):
        try:
            r = requests.delete(
                "http://127.0.0.1:{port}/groups/{gid}/primary".format(
                    port=port_agent,
                    gid=_enc_group_seg(group_id),
                ),
                headers=_group_auth_headers(),
                timeout=10,
            )
            return jsonify(r.json()), r.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── ACP external agent management ──

    @app.route("/proxy_acp_control", methods=["POST"])
    def proxy_acp_control():
        """代理 ACP /new 和 /stop 操作到后端。"""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"error": "未登录"}), 401
        try:
            data = request.get_json(silent=True) or {}
            data["user_id"] = user_id
            r = requests.post(
                "http://127.0.0.1:{port}/acp_control".format(port=port_agent),
                json=data,
                headers={"X-Internal-Token": internal_token},
                timeout=_PROXY_ACP_CONTROL_TIMEOUT_SEC,
            )
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {"error": r.text or "Unknown error"}
            return jsonify(resp_data), r.status_code
        except requests.exceptions.Timeout:
            return jsonify({"error": "ACP 操作超时"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_acp_status", methods=["POST"])
    def proxy_acp_status():
        """代理 ACP 状态查询到后端。"""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"error": "未登录"}), 401
        try:
            data = request.get_json(silent=True) or {}
            data["user_id"] = user_id
            r = requests.post(
                "http://127.0.0.1:{port}/acp_status".format(port=port_agent),
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
            return jsonify({"error": "状态查询超时"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_sessions_list", methods=["POST"])
    def proxy_sessions_list():
        """代理 sessions 列表查询：acpx sessions + http_agent_sessions。"""
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


    @app.route("/proxy_sessions_delete", methods=["POST"])
    def proxy_sessions_delete():
        """删除指定的 http_agent_session 记录。"""
        user_id = session.get("user_id", "")
        if not user_id:
            return jsonify({"error": "未登录"}), 401
        try:
            data = request.get_json(silent=True) or {}
            data["user_id"] = user_id
            r = requests.post(
                "http://127.0.0.1:{port}/sessions_delete".format(port=port_agent),
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
            return jsonify({"error": "删除超时"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500


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
