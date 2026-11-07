"""
app.py
Lightweight API Gateway - Flask server với Rate Limiting, Schema Validation và Telemetry.
"""

import time
from collections import deque
from datetime import datetime
from threading import Lock

from flask import Flask, jsonify, render_template, request

from gateway_core import SchemaValidator, SlidingWindowRateLimiter

app = Flask(__name__)
app.json.ensure_ascii = False  # Trả JSON tiếng Việt dễ đọc

# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------
INSPECT_SCHEMA = {
    "sender_id": {"type": "str", "required": True, "min_length": 3, "max_length": 50},
    "message_content": {"type": "str", "required": True, "min_length": 5, "max_length": 500},
    "risk_threshold": {"type": "int", "required": True, "min_value": 1, "max_value": 100},
}

rate_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=15)

# ---------------------------------------------------------------------------
# Telemetry (lưu trong bộ nhớ)
# ---------------------------------------------------------------------------
telemetry_lock = Lock()
request_logs = deque(maxlen=50)
stats = {
    "total": 0,
    "success_200": 0,
    "invalid_400_422": 0,
    "rate_limited_429": 0,
}


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def record(client_ip, status, started_at, detail):
    """Ghi nhận một request vào log lịch sử và bộ đếm thống kê."""
    latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
    with telemetry_lock:
        stats["total"] += 1
        if status == 200:
            stats["success_200"] += 1
        elif status in (400, 422):
            stats["invalid_400_422"] += 1
        elif status == 429:
            stats["rate_limited_429"] += 1

        request_logs.appendleft({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": client_ip,
            "status": status,
            "latency_ms": latency_ms,
            "detail": detail,
        })
    return latency_ms


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/v1/inspect", methods=["POST"])
def inspect():
    started_at = time.perf_counter()
    client_ip = get_client_ip()

    # Bước 1: Rate Limit
    allowed, retry_after = rate_limiter.is_allowed(client_ip)
    if not allowed:
        detail = f"Vượt quá giới hạn {rate_limiter.max_requests} request / {rate_limiter.window_seconds}s"
        latency_ms = record(client_ip, 429, started_at, detail)
        resp = jsonify({
            "status": "rejected",
            "code": 429,
            "error": "Too Many Requests",
            "message": detail,
            "retry_after_seconds": retry_after,
            "latency_ms": latency_ms,
        })
        resp.headers["Retry-After"] = str(max(1, int(retry_after + 0.999)))
        return resp, 429

    # Bước 2: Đọc JSON
    payload = request.get_json(silent=True)
    if payload is None:
        detail = "JSON không hợp lệ hoặc thiếu Content-Type: application/json"
        latency_ms = record(client_ip, 400, started_at, detail)
        return jsonify({
            "status": "rejected",
            "code": 400,
            "error": "Bad Request",
            "message": detail,
            "latency_ms": latency_ms,
        }), 400

    # Bước 3: Kiểm tra Schema
    is_valid, errors = SchemaValidator.validate(payload, INSPECT_SCHEMA)
    if not is_valid:
        detail = "; ".join(f"{e['field']}: {e['error']}" for e in errors)
        latency_ms = record(client_ip, 422, started_at, detail)
        return jsonify({
            "status": "rejected",
            "code": 422,
            "error": "Unprocessable Entity",
            "message": "Payload vi phạm quy tắc schema.",
            "errors": errors,
            "latency_ms": latency_ms,
        }), 422

    # Bước 4: Hợp lệ
    detail = f"OK - sender_id={payload['sender_id']}, risk_threshold={payload['risk_threshold']}"
    latency_ms = record(client_ip, 200, started_at, detail)
    return jsonify({
        "status": "accepted",
        "code": 200,
        "message": "Payload hợp lệ, đã được chuyển tiếp.",
        "data": {
            "sender_id": payload["sender_id"],
            "message_length": len(payload["message_content"]),
            "risk_threshold": payload["risk_threshold"],
        },
        "latency_ms": latency_ms,
    }), 200


@app.route("/api/v1/telemetry", methods=["GET"])
def telemetry():
    with telemetry_lock:
        return jsonify({
            "stats": dict(stats),
            "logs": list(request_logs),
            "rate_limit": {
                "max_requests": rate_limiter.max_requests,
                "window_seconds": rate_limiter.window_seconds,
            },
        })


@app.route("/api/v1/telemetry/clear", methods=["POST"])
def clear_telemetry():
    """Xóa toàn bộ nhật ký request và đặt lại bộ đếm thống kê."""
    with telemetry_lock:
        request_logs.clear()
        for k in stats:
            stats[k] = 0
    return jsonify({"status": "cleared", "message": "Đã xóa toàn bộ nhật ký và thống kê."}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
