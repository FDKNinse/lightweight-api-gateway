"""
gateway_core.py
Các thành phần lõi của Lightweight API Gateway:
  - SlidingWindowRateLimiter: giới hạn tần suất request theo IP (Sliding Window Log).
  - SchemaValidator: kiểm duyệt payload JSON bằng Python thuần.
"""

import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowRateLimiter:
    """Giới hạn mỗi IP tối đa `max_requests` request trong `window_seconds` giây gần nhất."""

    def __init__(self, max_requests=5, window_seconds=15):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Mỗi IP ánh xạ tới một deque chứa timestamp các request đã được chấp nhận
        self._timestamps = defaultdict(deque)
        self._lock = Lock()

    def is_allowed(self, client_ip):
        """
        Trả về tuple (allowed: bool, retry_after_seconds: float).
        retry_after_seconds = 0 khi request được chấp nhận.
        """
        now = time.monotonic()
        boundary = now - self.window_seconds

        with self._lock:
            window = self._timestamps[client_ip]

            # Xóa các timestamp đã nằm ngoài cửa sổ trượt
            while window and window[0] <= boundary:
                window.popleft()

            if len(window) >= self.max_requests:
                # Thời gian chờ đến khi timestamp cũ nhất rời khỏi cửa sổ
                retry_after_seconds = round(window[0] + self.window_seconds - now, 2)
                return False, max(retry_after_seconds, 0.0)

            window.append(now)
            return True, 0.0


class SchemaValidator:
    """Bộ kiểm duyệt schema tối giản, không phụ thuộc thư viện ngoài."""

    TYPE_MAP = {"str": str, "int": int}

    @staticmethod
    def validate(payload, schema):
        """
        Kiểm tra `payload` theo `schema`.

        schema dạng:
            {
                "field_name": {
                    "type": "str" | "int",
                    "required": True,
                    "min_length": 3, "max_length": 50,   # cho str
                    "min_value": 1,  "max_value": 100,   # cho int
                },
                ...
            }

        Trả về tuple (is_valid: bool, errors: list[dict]).
        """
        errors = []

        if not isinstance(payload, dict):
            return False, [{"field": None, "error": "Payload phải là một JSON object."}]

        for field, rules in schema.items():
            # 1. Trường bắt buộc
            if field not in payload or payload[field] is None:
                if rules.get("required", False):
                    errors.append({"field": field, "error": "Trường bắt buộc bị thiếu."})
                continue

            value = payload[field]
            expected = rules.get("type")

            # 2. Kiểu dữ liệu
            if expected == "str":
                if not isinstance(value, str):
                    errors.append({"field": field, "error": f"Phải là kiểu chuỗi (str), nhận được {type(value).__name__}."})
                    continue
            elif expected == "int":
                # bool là lớp con của int trong Python -> phải chặn riêng
                if isinstance(value, bool) or not isinstance(value, int):
                    errors.append({"field": field, "error": f"Phải là số nguyên (int), nhận được {type(value).__name__}."})
                    continue

            # 3. Độ dài chuỗi
            if isinstance(value, str):
                length = len(value)
                min_len = rules.get("min_length")
                max_len = rules.get("max_length")
                if min_len is not None and length < min_len:
                    errors.append({"field": field, "error": f"Độ dài tối thiểu {min_len} ký tự (hiện tại {length})."})
                if max_len is not None and length > max_len:
                    errors.append({"field": field, "error": f"Độ dài tối đa {max_len} ký tự (hiện tại {length})."})

            # 4. Miền giá trị số
            if isinstance(value, int) and not isinstance(value, bool):
                min_val = rules.get("min_value")
                max_val = rules.get("max_value")
                if min_val is not None and value < min_val:
                    errors.append({"field": field, "error": f"Giá trị tối thiểu là {min_val} (hiện tại {value})."})
                if max_val is not None and value > max_val:
                    errors.append({"field": field, "error": f"Giá trị tối đa là {max_val} (hiện tại {value})."})

        return len(errors) == 0, errors
