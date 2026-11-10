"""Authenticated controls for explicit host component downloads."""
from flask import jsonify, request, session
from ops.components import component_status, start_install
from urllib.parse import urlsplit


def register_component_routes(app):
    @app.route("/proxy_components/<name>", methods=["GET", "POST"])
    def components(name):
        if not session.get("user_id"):
            return jsonify(error="请先登录"), 401
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="不允许跨站安装"), 403
            if request.headers.get("X-Requested-With") != "ClawCross":
                return jsonify(error="需要从组件安装按钮发起"), 403
        try:
            return jsonify(start_install(name) if request.method == "POST" else component_status(name))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
