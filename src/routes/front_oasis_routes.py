"""
Flask 前端 OASIS 论坛代理路由模块

为 Flask 前端提供 OASIS 论坛相关的代理路由：
- /proxy_oasis/topics：代理话题列表/创建
- /proxy_oasis/topics/<topic_id>：代理话题详情
"""

from functools import partial

from flask import Response, jsonify, request, session
import requests

from utils.logging_utils import get_logger

logger = get_logger("front_oasis_routes")


def _parse_oasis_json_response(
    r: requests.Response,
    *,
    context: str,
    oasis_base_url: str = "",
):
    """Parse OASIS HTTP body as JSON. Never raises — returns (payload, status_code).

    When the upstream body is empty or not JSON (HTML error page, wrong port, etc.),
    returns a dict error payload and 502 so the frontend gets a clear message instead
    of ``Expecting value: line 1 column 1`` from ``r.json()``."""
    base = (oasis_base_url or "").rstrip("/") or "http://127.0.0.1:51202"
    hint = (
        f"本机自检: 浏览器打开 {base}/docs 应出现 API 文档；"
        f"或终端执行 curl -sS '{base}/openapi.json' | head -c 80"
    )
    raw = r.text or ""
    if not raw.strip():
        logger.warning(
            "OASIS empty body context=%s status=%s url=%s",
            context,
            r.status_code,
            getattr(r, "url", ""),
        )
        return (
            {
                "error": "OASIS 返回空响应（无法解析为 JSON）",
                "detail": (
                    f"请确认 OASIS 论坛进程已启动，且 front 的 PORT_OASIS 与之一致 "
                    f"（当前上游 HTTP {r.status_code}）"
                ),
                "upstream_status": r.status_code,
                "hint": hint,
            },
            502,
        )
    try:
        data = r.json()
    except ValueError:
        preview = raw.strip()[:800]
        logger.warning(
            "OASIS non-JSON body context=%s status=%s preview=%r",
            context,
            r.status_code,
            preview[:240],
        )
        return (
            {
                "error": "OASIS 返回了非 JSON 内容",
                "detail": preview,
                "upstream_status": r.status_code,
                "hint": hint,
            },
            502,
        )
    if not isinstance(data, (dict, list)):
        data = {"value": data}
    return data, r.status_code


def register_oasis_routes(app, *, oasis_base_url: str) -> None:
    """Register OASIS proxy routes for Flask frontend."""
    _parse = partial(_parse_oasis_json_response, oasis_base_url=oasis_base_url)

    @app.route("/proxy_oasis/topics")
    def proxy_oasis_topics():
        user_id = session.get("user_id", "")
        try:
            logger.info("Fetching OASIS topics for user=%s", user_id)
            r = requests.get(
                "{base}/topics".format(base=oasis_base_url),
                params={"user_id": user_id},
                timeout=10,
            )
            data, st = _parse(r, context="list_topics")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error fetching topics for user=%s: %s", user_id, e)
            return jsonify([]), 200

    @app.route("/proxy_oasis/topics", methods=["POST"])
    def proxy_oasis_create_topic():
        """Create a new OASIS topic/workflow from frontend."""
        user_id = session.get("user_id", "")
        try:
            headers = {"Content-Type": "application/json"}
            body = request.get_json(silent=True) or {}
            # Inject user_id
            body["user_id"] = user_id
            r = requests.post(
                "{base}/topics".format(base=oasis_base_url),
                json=body,
                headers=headers,
                timeout=30,
            )
            data, st = _parse(r, context="create_topic")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error creating topic for user=%s: %s", user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>")
    def proxy_oasis_topic_detail(topic_id):
        user_id = session.get("user_id", "")
        try:
            r = requests.get(
                "{base}/topics/{topic_id}".format(base=oasis_base_url, topic_id=topic_id),
                params={"user_id": user_id},
                timeout=10,
            )
            data, st = _parse(r, context="topic_detail")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error fetching topic detail %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/agent-stats")
    def proxy_oasis_topic_agent_stats(topic_id):
        user_id = session.get("user_id", "")
        try:
            r = requests.get(
                "{base}/topics/{topic_id}/agent-stats".format(base=oasis_base_url, topic_id=topic_id),
                params={"user_id": user_id},
                timeout=10,
            )
            data, st = _parse(r, context="topic_agent_stats")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error fetching topic agent stats %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/actions")
    def proxy_oasis_topic_actions(topic_id):
        user_id = session.get("user_id", "")
        params = {
            "user_id": user_id,
            "limit": request.args.get("limit", "100"),
            "offset": request.args.get("offset", "0"),
            "agent": request.args.get("agent", ""),
        }
        try:
            r = requests.get(
                "{base}/topics/{topic_id}/actions".format(base=oasis_base_url, topic_id=topic_id),
                params=params,
                timeout=10,
            )
            data, st = _parse(r, context="topic_actions")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error fetching topic actions %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/interview", methods=["POST"])
    def proxy_oasis_topic_interview(topic_id):
        user_id = session.get("user_id", "")
        try:
            headers = {"Content-Type": "application/json"}
            body = request.get_json(silent=True) or {}
            body["user_id"] = user_id
            r = requests.post(
                "{base}/topics/{topic_id}/interview".format(base=oasis_base_url, topic_id=topic_id),
                json=body,
                headers=headers,
                timeout=60,
            )
            data, st = _parse(r, context="topic_interview")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error interviewing topic agent %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/posts", methods=["POST"])
    def proxy_oasis_add_topic_post(topic_id):
        user_id = session.get("user_id", "")
        try:
            headers = {"Content-Type": "application/json"}
            body = request.get_json(silent=True) or {}
            body["user_id"] = user_id
            body.setdefault("author", user_id or "主持人")
            r = requests.post(
                "{base}/topics/{topic_id}/posts".format(base=oasis_base_url, topic_id=topic_id),
                json=body,
                headers=headers,
                timeout=30,
            )
            data, st = _parse(r, context="topic_posts")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error adding live post to topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/human-reply", methods=["POST"])
    def proxy_oasis_human_reply(topic_id):
        user_id = session.get("user_id", "")
        try:
            headers = {"Content-Type": "application/json"}
            body = request.get_json(silent=True) or {}
            body["user_id"] = user_id
            body.setdefault("author", user_id or "主持人")
            r = requests.post(
                "{base}/topics/{topic_id}/human-reply".format(base=oasis_base_url, topic_id=topic_id),
                json=body,
                headers=headers,
                timeout=30,
            )
            data, st = _parse(r, context="human_reply")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error submitting human reply to topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/swarm/refresh", methods=["POST"])
    def proxy_oasis_refresh_topic_swarm(topic_id):
        user_id = session.get("user_id", "")
        try:
            r = requests.post(
                "{base}/topics/{topic_id}/swarm/refresh".format(base=oasis_base_url, topic_id=topic_id),
                params={"user_id": user_id},
                timeout=30,
            )
            data, st = _parse(r, context="swarm_refresh")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error refreshing swarm for topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/report/ask", methods=["POST"])
    def proxy_oasis_ask_topic_report(topic_id):
        user_id = session.get("user_id", "")
        try:
            headers = {"Content-Type": "application/json"}
            body = request.get_json(silent=True) or {}
            body["user_id"] = user_id
            r = requests.post(
                "{base}/topics/{topic_id}/report/ask".format(base=oasis_base_url, topic_id=topic_id),
                json=body,
                headers=headers,
                timeout=60,
            )
            data, st = _parse(r, context="report_ask")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error asking report for topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/stream")
    def proxy_oasis_topic_stream(topic_id):
        user_id = session.get("user_id", "")
        try:
            r = requests.get(
                "{base}/topics/{topic_id}/stream".format(base=oasis_base_url, topic_id=topic_id),
                params={"user_id": user_id},
                stream=True,
                timeout=300,
            )
            if r.status_code != 200:
                return jsonify({"error": "OASIS returned {code}".format(code=r.status_code)}), r.status_code

            def generate():
                for line in r.iter_lines(decode_unicode=True):
                    if line:
                        yield line + "\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        except Exception as e:
            logger.warning("Error streaming topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e)}), 500

    @app.route("/proxy_oasis/experts")
    def proxy_oasis_experts():
        user_id = session.get("user_id", "")
        try:
            r = requests.get(
                "{base}/experts".format(base=oasis_base_url),
                params={"user_id": user_id},
                timeout=10,
            )
            data, st = _parse(r, context="experts")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error fetching experts for user=%s: %s", user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/cancel", methods=["POST"])
    def proxy_oasis_cancel_topic(topic_id):
        user_id = session.get("user_id", "")
        try:
            r = requests.delete(
                "{base}/topics/{topic_id}".format(base=oasis_base_url, topic_id=topic_id),
                params={"user_id": user_id},
                timeout=10,
            )
            data, st = _parse(r, context="cancel_topic")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error cancel topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics/<topic_id>/purge", methods=["POST"])
    def proxy_oasis_purge_topic(topic_id):
        user_id = session.get("user_id", "")
        try:
            r = requests.post(
                "{base}/topics/{topic_id}/purge".format(base=oasis_base_url, topic_id=topic_id),
                params={"user_id": user_id},
                timeout=10,
            )
            data, st = _parse(r, context="purge_topic")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error purging topic %s for user=%s: %s", topic_id, user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502

    @app.route("/proxy_oasis/topics", methods=["DELETE"])
    def proxy_oasis_purge_all_topics():
        user_id = session.get("user_id", "")
        try:
            r = requests.delete(
                "{base}/topics".format(base=oasis_base_url),
                params={"user_id": user_id},
                timeout=30,
            )
            data, st = _parse(r, context="purge_all_topics")
            return jsonify(data), st
        except requests.RequestException as e:
            logger.warning("Error purging all topics for user=%s: %s", user_id, e)
            return jsonify({"error": str(e), "detail": "无法连接 OASIS"}), 502
