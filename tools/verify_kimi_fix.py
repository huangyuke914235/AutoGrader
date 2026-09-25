# -*- coding: utf-8 -*-
"""端到端验证「Kimi 温度参数」修复 —— 走真实 HTTP + 真实 OpenAI SDK

为什么不用单元测试里的假客户端就收工：
那一层验证的是「我们有没有按规则传参」，mock 是我们自己写的。
这次的故障恰恰是**对平台行为的理解错了** —— 用自己的假设去验证自己的假设，
等于没有验证。所以这里起一个本地 HTTP 服务，逐字复刻 Kimi 官方文档写的行为：

    kimi-k2.x / k3 的 temperature、top_p、n 为固定值，传入其他值会报错
    （platform.kimi.com/docs/api/models-overview）

然后让**真实的 OpenAI SDK** 去打它。SDK 怎么解析 400、抛什么异常、
带不带 status_code，全部照线上来。

另外设置了【反向对照】：故意用底层方法给 Kimi 传一次 temperature，
确认这个假端点**真的会拒绝**。如果它连非法请求都放行，
那「修复后通过」就是个假绿灯 —— 这一步是整套验证的诚信底线。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI                                          # noqa: E402

import llm                                                         # noqa: E402
from models import SelfCheckPayload                                # noqa: E402

# Kimi 的合法取值：思考模式 1.0 / 非思考模式 0.6。其余一律拒绝。
VALID_TEMP = {1.0, 0.6}

_models = [{"id": "kimi-k2.6", "object": "model"},
           {"id": "kimi-k3", "object": "model"}]
_served = []      # 记录服务端收到的请求，用于反查


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                      # 别把请求日志刷到终端

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Key 无效时连模型列表都列不出来 —— 真实平台就是这样，
        # 假服务端若不跟着做严格，"诊断会提前终止" 这条结论就成了空话。
        if "sk-bad" in self.headers.get("Authorization", ""):
            self._send(401, {"error": {"message": "Invalid Authorization",
                                       "type": "authentication_error"}})
            return
        if self.path.endswith("/models"):
            self._send(200, {"object": "list", "data": _models})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        model = payload.get("model", "")
        auth = self.headers.get("Authorization", "")
        _served.append(payload)

        # 模拟「Key 无效」：用于验证诊断函数的 401 分诊结论
        if "sk-bad" in auth:
            self._send(401, {"error": {"message": "Invalid Authorization",
                                       "type": "authentication_error"}})
            return

        # 逐字复刻 Kimi 的固定参数规则
        if model.startswith("kimi-"):
            if "temperature" in payload and payload["temperature"] not in VALID_TEMP:
                self._send(400, {"error": {
                    "message": f"invalid_request_error: temperature must be "
                               f"{sorted(VALID_TEMP)} for {model}, got "
                               f"{payload['temperature']}",
                    "type": "invalid_request_error"}})
                return
            if "top_p" in payload and payload["top_p"] != 0.95:
                self._send(400, {"error": {
                    "message": "invalid_request_error: top_p is fixed to 0.95",
                    "type": "invalid_request_error"}})
                return

        self._send(200, {
            "id": "chatcmpl-x", "object": "chat.completion", "created": 0,
            "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant",
                                     "content": '{"items": []}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                      "total_tokens": 2}})


def _start():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def main():
    srv, url = _start()
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" —— {detail}" if detail else ""))

    try:
        # ===== 反向对照：先确认这个假端点真的会拒绝非法 temperature =====
        # 如果这一步是「成功」的，说明 mock 没起作用，下面所有结论都不可信。
        raw = OpenAI(api_key="sk-x", base_url=url, timeout=30)
        rejected = False
        try:
            raw.chat.completions.create(
                model="kimi-k2.6",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
            )
        except Exception as e:
            rejected = getattr(e, "status_code", None) == 400
        check("【对照】未修复的写法会被平台拒绝", rejected,
              "" if rejected else "假端点没有拒绝 temperature=0，验证失效，下面结论不可信")

        if not rejected:
            print("\n验证环境不可信，停止。")
            return 1

        # ===== ① 修复后：call_json 走 Kimi 应当通过 =====
        try:
            llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                          api_key="sk-x", base_url=url, model="kimi-k2.6")
            check("call_json 走 kimi-k2.6", True, "未触发 sampling 参数拒绝")
        except Exception as e:
            check("call_json 走 kimi-k2.6", False, f"{type(e).__name__}: {str(e)[:160]}")

        # 发出去的请求里确实没有 temperature —— 不是靠运气过的
        last = [p for p in _served if p.get("model") == "kimi-k2.6"][-1]
        check("实际请求未携带 temperature", "temperature" not in last,
              f"实际参数：{sorted(last.keys())}")

        # ===== ② 不影响别家：DeepSeek 仍锁 temperature=0 =====
        try:
            llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                          api_key="sk-x", base_url=url, model="deepseek-chat")
            check("call_json 走 deepseek-chat", True)
        except Exception as e:
            check("call_json 走 deepseek-chat", False, str(e)[:160])
        _ds = [p for p in _served if p.get("model") == "deepseek-chat"][-1]
        check("DeepSeek 仍传 temperature=0", _ds.get("temperature") == 0.0)

        # ===== ③ 诊断：健康通道 =====
        d = llm.diagnose(api_key="sk-x", base_url=url, model="kimi-k2.6")
        check("诊断 · 健康通道", d["ok"] is True and len(d["steps"]) == 4,
              " / ".join(f"{'✓' if ok else '✗'}{n}" for n, ok, _ in d["steps"]))
        check("诊断 · 列出可用模型", d["models"] == ["kimi-k2.6", "kimi-k3"],
              str(d["models"]))

        # ===== ④ 诊断：Key 无效要给出两端点的分诊 =====
        d2 = llm.diagnose(api_key="sk-bad-key-9999", base_url=url, model="kimi-k2.6")
        check("诊断 · 无效 Key 判 401", d2["ok"] is False and len(d2["steps"]) == 1)
        check("诊断 · 提示两站端点", "api.moonshot.ai" in d2.get("hint", ""))
        check("诊断 · 不泄露密钥", "9999" not in str(d2))

        # ===== ⑤ 诊断：模型名不在名下 =====
        d3 = llm.diagnose(api_key="sk-x", base_url=url, model="moonshot-v1-8k")
        check("诊断 · 退役模型被点名", d3["steps"][1][1] is False
              and "kimi-k2.6" in d3.get("hint", ""))

    finally:
        srv.shutdown()

    bad = [n for n, ok, _ in checks if not ok]
    print(f"\n{'=' * 60}")
    if bad:
        print(f"❌ {len(bad)}/{len(checks)} 项未通过：" + "、".join(bad))
        return 1
    print(f"✅ {len(checks)} 项全部通过 —— Kimi 通道已可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
