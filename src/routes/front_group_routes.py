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


def _enc_group_seg(segment: str) -> str:
    """Path segment for FastAPI /groups/{group_id}/... (Unicode, #, :, etc.)."""
    return quote(segment or "", safe="")

# Agent /acp_control may run acpx OpenClaw exec /new for up to ~180s; a short proxy
# timeout makes the UI show "超时" while the backend still succeeds.
_PROXY_ACP_CONTROL_TIMEOUT_SEC = 240


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
