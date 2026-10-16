"""CPU-only stand-in for a vLLM OpenAI server (compose profile `stub`).

For laptops/CI without a GPU: lets the gateway, playground, usage logging and dashboards be exercised end to end.
It speaks just enough of the vLLM surface: /health, /v1/models, /v1/chat/completions (+ SSE streaming),
/v1/completions, /v1/embeddings and a /metrics endpoint with vLLM-named series. It does NOT run a model.
"""

import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.environ.get("STUB_MODEL", "stub-model")
PORT = int(os.environ.get("PORT", "8000"))
DELAY = float(os.environ.get("STUB_TOKEN_DELAY_MS", "15")) / 1000

_lock = threading.Lock()
_state = {"running": 0, "success": 0, "prompt_tokens": 0, "gen_tokens": 0, "lat_sum": 0.0, "lat_count": 0}
_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]
_lat_buckets = [0] * len(_BUCKETS)


def _observe(seconds: float) -> None:
    with _lock:
        _state["lat_sum"] += seconds
        _state["lat_count"] += 1
        for i, b in enumerate(_BUCKETS):
            if seconds <= b:
                _lat_buckets[i] += 1


def _metrics() -> str:
    lbl = f'model_name="{MODEL}"'
    with _lock:
        s = dict(_state)
        buckets = list(_lat_buckets)
    out = [
        "# TYPE vllm:num_requests_running gauge", f"vllm:num_requests_running{{{lbl}}} {s['running']}",
        "# TYPE vllm:num_requests_waiting gauge", f"vllm:num_requests_waiting{{{lbl}}} 0",
        "# TYPE vllm:gpu_cache_usage_perc gauge", f"vllm:gpu_cache_usage_perc{{{lbl}}} {random.random() * 0.2:.4f}",
        "# TYPE vllm:request_success_total counter", f'vllm:request_success_total{{{lbl},finished_reason="stop"}} {s["success"]}',
        "# TYPE vllm:prompt_tokens_total counter", f"vllm:prompt_tokens_total{{{lbl}}} {s['prompt_tokens']}",
        "# TYPE vllm:generation_tokens_total counter", f"vllm:generation_tokens_total{{{lbl}}} {s['gen_tokens']}",
        "# TYPE vllm:e2e_request_latency_seconds histogram",
    ]
    for b, c in zip(_BUCKETS, buckets):  # _observe() already stores cumulative bucket counts
        out.append(f'vllm:e2e_request_latency_seconds_bucket{{{lbl},le="{b}"}} {c}')
    out.append(f'vllm:e2e_request_latency_seconds_bucket{{{lbl},le="+Inf"}} {s["lat_count"]}')
    out.append(f"vllm:e2e_request_latency_seconds_sum{{{lbl}}} {s['lat_sum']:.6f}")
    out.append(f"vllm:e2e_request_latency_seconds_count{{{lbl}}} {s['lat_count']}")
    out.append("# TYPE vllm:time_to_first_token_seconds histogram")
    out.append(f'vllm:time_to_first_token_seconds_bucket{{{lbl},le="+Inf"}} {s["lat_count"]}')
    out.append(f"vllm:time_to_first_token_seconds_sum{{{lbl}}} {s['lat_count'] * 0.05:.6f}")
    out.append(f"vllm:time_to_first_token_seconds_count{{{lbl}}} {s['lat_count']}")
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        return

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "stub"}]})
        elif self.path == "/metrics":
            raw = _metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error", "code": "not_found"}})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": {"message": "invalid JSON", "type": "invalid_request_error", "code": "bad_json"}})
        if self.path == "/v1/embeddings":
            inputs = req.get("input", [])
            inputs = [inputs] if isinstance(inputs, str) else inputs
            data = [{"object": "embedding", "index": i, "embedding": [0.0] * 8} for i, _ in enumerate(inputs)]
            return self._json(200, {"object": "list", "data": data, "model": MODEL,
                                    "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)}})
        if self.path not in ("/v1/chat/completions", "/v1/completions"):
            return self._json(404, {"error": {"message": "not found", "type": "invalid_request_error", "code": "not_found"}})

        started = time.time()
        text = "This is a response from the SLM Studio vLLM stub. No model is running."
        words = text.split(" ")
        prompt_tokens = max(1, len(json.dumps(req.get("messages") or req.get("prompt") or "")) // 4)
        with _lock:
            _state["running"] += 1
        try:
            chat = self.path.endswith("chat/completions")
            if req.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def chunk(payload: str) -> None:
                    data = payload.encode()
                    self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()

                for w in words:
                    time.sleep(DELAY)
                    choice = {"index": 0, "finish_reason": None}
                    if chat:
                        choice["delta"] = {"content": w + " "}
                    else:
                        choice["text"] = w + " "
                    event = {"choices": [choice], "model": MODEL,
                             "object": "chat.completion.chunk" if chat else "text_completion"}
                    chunk(f"data: {json.dumps(event)}\n\n")
                usage = {"prompt_tokens": prompt_tokens, "completion_tokens": len(words), "total_tokens": prompt_tokens + len(words)}
                if (req.get("stream_options") or {}).get("include_usage"):
                    chunk(f"data: {json.dumps({'choices': [], 'model': MODEL, 'usage': usage})}\n\n")
                chunk("data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
            else:
                time.sleep(DELAY * len(words))
                choice = ({"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"} if chat
                          else {"index": 0, "text": text, "finish_reason": "stop"})
                self._json(200, {"id": f"stub-{int(started * 1000)}", "object": "chat.completion" if chat else "text_completion",
                                 "created": int(started), "model": MODEL, "choices": [choice],
                                 "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(words),
                                           "total_tokens": prompt_tokens + len(words)}})
        finally:
            with _lock:
                _state["running"] -= 1
                _state["success"] += 1
                _state["prompt_tokens"] += prompt_tokens
                _state["gen_tokens"] += len(words)
            _observe(time.time() - started)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
