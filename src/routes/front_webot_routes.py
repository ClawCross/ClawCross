"""
Flask frontend proxy routes for WeBot runtime APIs.
"""

from flask import jsonify, request, session
import requests


def register_webot_routes(
    app,
    *,
    port_agent: int,
    internal_token: str,
) -> None:
    base_url = f"http://127.0.0.1:{port_agent}"

    def _internal_auth_headers():
        return {"X-Internal-Token": internal_token}

    @app.route("/proxy_webot_subagents")
    def proxy_webot_subagents():
        user_id = session.get("user_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/subagents",
                params={"user_id": user_id},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_subagent_history", methods=["POST"])
    def proxy_webot_subagent_history():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/subagents/history",
                json={
                    "user_id": user_id,
                    "agent_ref": body.get("agent_ref", ""),
                    "limit": body.get("limit", 12),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_subagent_cancel", methods=["POST"])
    def proxy_webot_subagent_cancel():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/subagents/cancel",
                json={
                    "user_id": user_id,
                    "agent_ref": body.get("agent_ref", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_tool_policy")
    def proxy_webot_tool_policy():
        user_id = session.get("user_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/tool-policy",
                params={"user_id": user_id},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_tool_policy", methods=["POST"])
    def proxy_webot_tool_policy_update():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/tool-policy",
                json={
                    "user_id": user_id,
                    "policy": body.get("policy") or {},
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_tool_approvals")
    def proxy_webot_tool_approvals():
        user_id = session.get("user_id", "")
        status = request.args.get("status", "pending")
        session_id = request.args.get("session_id", "")
        limit = request.args.get("limit", "20")
        try:
            response = requests.get(
                f"{base_url}/webot/tool-approvals",
                params={
                    "user_id": user_id,
                    "status": status,
                    "session_id": session_id,
                    "limit": limit,
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc), "approvals": []}), 500

    @app.route("/proxy_webot_session_runtime")
    def proxy_webot_session_runtime():
        user_id = session.get("user_id", "")
        session_id = request.args.get("session_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/session-runtime",
                params={"user_id": user_id, "session_id": session_id},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_session_mode", methods=["POST"])
    def proxy_webot_session_mode():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/session-mode",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "mode": body.get("mode", "execute"),
                    "reason": body.get("reason", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_lsp", methods=["POST"])
    def proxy_webot_lsp():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/lsp",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "file": body.get("file", ""),
                    "op": body.get("op", "diagnostics"),
                    "line": body.get("line", 0),
                    "col": body.get("col", 0),
                    "new_name": body.get("new_name", ""),
                    "timeout_seconds": body.get("timeout_seconds", 30),
                    "max_diagnostics": body.get("max_diagnostics", 50),
                },
                headers=_internal_auth_headers(),
                timeout=45,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_workflow_presets")
    def proxy_webot_workflow_presets():
        user_id = session.get("user_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/workflow-presets",
                params={"user_id": user_id},
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_workflow_apply", methods=["POST"])
    def proxy_webot_workflow_apply():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/workflow-presets/apply",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "preset_id": body.get("preset_id", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_session_inbox")
    def proxy_webot_session_inbox():
        user_id = session.get("user_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/session-inbox",
                params={
                    "user_id": user_id,
                    "session_id": request.args.get("session_id", ""),
                    "target_ref": request.args.get("target_ref", ""),
                    "status": request.args.get("status", "queued"),
                    "limit": request.args.get("limit", 20),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_session_inbox_send", methods=["POST"])
    def proxy_webot_session_inbox_send():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/session-inbox/send",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "target_ref": body.get("target_ref", ""),
                    "body": body.get("body", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_session_inbox_deliver", methods=["POST"])
    def proxy_webot_session_inbox_deliver():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/session-inbox/deliver",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "target_ref": body.get("target_ref", ""),
                    "limit": body.get("limit", 20),
                    "force": bool(body.get("force", False)),
                },
                headers=_internal_auth_headers(),
                timeout=20,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_run_interrupt", methods=["POST"])
    def proxy_webot_run_interrupt():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/runs/interrupt",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "run_id": body.get("run_id", ""),
                    "agent_ref": body.get("agent_ref", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_session_goals")
    def proxy_webot_session_goals():
        user_id = session.get("user_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/session-goals",
                params={
                    "user_id": user_id,
                    "session_id": request.args.get("session_id", ""),
                    "status": request.args.get("status", ""),
                    "limit": request.args.get("limit", 20),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc), "goals": []}), 500

    @app.route("/proxy_webot_session_goal", methods=["POST"])
    def proxy_webot_session_goal():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/session-goals",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "goal_id": body.get("goal_id", ""),
                    "title": body.get("title", ""),
                    "description": body.get("description", ""),
                    "status": body.get("status", "active"),
                    "priority": body.get("priority", "normal"),
                    "parent_goal_id": body.get("parent_goal_id", ""),
                    "owner_session": body.get("owner_session", ""),
                    "metrics": body.get("metrics") or {},
                    "budget_tokens": body.get("budget_tokens", 0),
                    "spent_tokens": body.get("spent_tokens", 0),
                    "budget_usd": body.get("budget_usd", 0),
                    "spent_usd": body.get("spent_usd", 0),
                    "metadata": body.get("metadata") or {},
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_session_goal_heartbeat", methods=["POST"])
    def proxy_webot_session_goal_heartbeat():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/session-goals/heartbeat",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "goal_id": body.get("goal_id", ""),
                    "heartbeat_status": body.get("heartbeat_status", "active"),
                    "report": body.get("report", ""),
                    "spent_tokens_delta": body.get("spent_tokens_delta", 0),
                    "spent_usd_delta": body.get("spent_usd_delta", 0),
                    "metadata": body.get("metadata") or {},
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_claude_code_status")
    def proxy_webot_claude_code_status():
        user_id = session.get("user_id", "")
        try:
            response = requests.get(
                f"{base_url}/webot/claude-code/status",
                params={
                    "user_id": user_id,
                    "session_id": request.args.get("session_id", "default"),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_claude_keepalive", methods=["POST"])
    def proxy_webot_claude_keepalive():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/claude-code/keepalive",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "enabled": bool(body.get("enabled", False)),
                    "prompt": body.get("prompt", "ping"),
                    "model": body.get("model", ""),
                    "timezone": body.get("timezone", ""),
                    "start_time": body.get("start_time", "06:00"),
                    "sleep_time": body.get("sleep_time", "23:00"),
                    "weekdays": body.get("weekdays", "MTWRFSU"),
                    "use_caffeinate": bool(body.get("use_caffeinate", False)),
                    "force_sleep_at_quiet_hours": bool(body.get("force_sleep_at_quiet_hours", False)),
                    "monitor_command": body.get("monitor_command", "claude-monitor --clear"),
                    "timeout_seconds": body.get("timeout_seconds", 90),
                    "metadata": body.get("metadata") or {},
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_claude_probe", methods=["POST"])
    def proxy_webot_claude_probe():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/claude-code/probe",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "prompt": body.get("prompt", "Reply only CLAUDE_ACP_OK"),
                    "session_name": body.get("session_name", ""),
                    "timeout_seconds": body.get("timeout_seconds", 90),
                },
                headers=_internal_auth_headers(),
                timeout=max(20, int(body.get("timeout_seconds", 90)) + 10),
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_claude_kickoff", methods=["POST"])
    def proxy_webot_claude_kickoff():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/claude-code/kickoff",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "prompt": body.get("prompt", "ping"),
                    "session_name": body.get("session_name", ""),
                    "timeout_seconds": body.get("timeout_seconds", 90),
                    "model": body.get("model", ""),
                    "use_acp": bool(body.get("use_acp", True)),
                },
                headers=_internal_auth_headers(),
                timeout=max(20, int(body.get("timeout_seconds", 90)) + 10),
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_voice", methods=["POST"])
    def proxy_webot_voice():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/voice",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "enabled": bool(body.get("enabled", False)),
                    "auto_read_aloud": bool(body.get("auto_read_aloud", False)),
                    "last_transcript": body.get("last_transcript", ""),
                    "tts_model": body.get("tts_model", ""),
                    "tts_voice": body.get("tts_voice", ""),
                    "stt_model": body.get("stt_model", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_bridge_attach", methods=["POST"])
    def proxy_webot_bridge_attach():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/bridge/attach",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "role": body.get("role", "viewer"),
                    "label": body.get("label", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_bridge_detach", methods=["POST"])
    def proxy_webot_bridge_detach():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/bridge/detach",
                json={
                    "user_id": user_id,
                    "bridge_id": body.get("bridge_id", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_kairos", methods=["POST"])
    def proxy_webot_kairos():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/kairos",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "enabled": bool(body.get("enabled", False)),
                    "reason": body.get("reason", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_dream", methods=["POST"])
    def proxy_webot_dream():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/dream",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "reason": body.get("reason", ""),
                },
                headers=_internal_auth_headers(),
                timeout=30,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_buddy", methods=["POST"])
    def proxy_webot_buddy():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/buddy",
                json={
                    "user_id": user_id,
                    "session_id": body.get("session_id", ""),
                    "action": body.get("action", "pet"),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/proxy_webot_tool_approval_resolve", methods=["POST"])
    def proxy_webot_tool_approval_resolve():
        user_id = session.get("user_id", "")
        body = request.get_json(force=True) if request.is_json else {}
        try:
            response = requests.post(
                f"{base_url}/webot/tool-approvals/resolve",
                json={
                    "user_id": user_id,
                    "approval_id": body.get("approval_id", ""),
                    "action": body.get("action", "approve"),
                    "reason": body.get("reason", ""),
                    "remember": bool(body.get("remember", False)),
                    "session_id": body.get("session_id", ""),
                },
                headers=_internal_auth_headers(),
                timeout=15,
            )
            return jsonify(response.json()), response.status_code
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
