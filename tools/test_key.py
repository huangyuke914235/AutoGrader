# -*- coding: utf-8 -*-
"""
模型 API 连通性自检（D1 用，2 分钟）

用法：
    # 先把 key 写进项目根目录的 .env（别提交！），然后：
    python tools/test_key.py

通过标准：打印出 "OK" 和一句模型回复。
不通过：照着终端提示改 .env。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 极简 .env 读取（不依赖 python-dotenv，避免多装一个包）
env_path = os.path.join(ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = os.getenv("LLM_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "")
MODEL = os.getenv("LLM_MODEL", "")

print("当前配置：")
print(f"  BASE_URL = {BASE_URL or '(空)'}")
print(f"  MODEL    = {MODEL or '(空)'}")
print(f"  API_KEY  = {'已设置(' + str(len(API_KEY)) + '位)' if API_KEY else '(空)'}\n")

if not API_KEY or not BASE_URL or not MODEL:
    print("配置不完整。请在项目根目录建 .env，内容形如：\n")
    print("LLM_API_KEY=sk-xxxxxxxx")
    print("LLM_BASE_URL=https://api.deepseek.com/v1")
    print("LLM_MODEL=deepseek-chat\n")
    sys.exit(1)

try:
    from openai import OpenAI
except ImportError:
    print("缺少 openai 包，请先执行：pip install openai")
    sys.exit(1)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

print("测试 1/2 · 普通对话 …")
t0 = time.time()
try:
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "只回复两个字：你好"}],
        temperature=0,
    )
    print(f"  OK  用时 {time.time()-t0:.1f}s  回复：{r.choices[0].message.content}\n")
except Exception as e:
    print(f"  失败：{e}\n")
    print("排查顺序：① key 是否复制完整 ② BASE_URL 是否带 /v1 ③ 账户是否有余额 ④ 网络能否访问该域名")
    sys.exit(1)

print("测试 2/2 · JSON 结构化输出（这是本项目的命脉）…")
t0 = time.time()
try:
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user",
                   "content": '请只输出 JSON：{"verdict":"hit","score":10,"confidence":0.9,'
                              '"reason":"测试","evidence":[{"quote":"测试片段"}]}'}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = r.choices[0].message.content
    import json
    obj = json.loads(raw)
    need = ["verdict", "score", "confidence", "reason", "evidence"]
    miss = [k for k in need if k not in obj]
    print(f"  OK  用时 {time.time()-t0:.1f}s")
    print(f"  返回：{raw[:120]}")
    if miss:
        print(f"  ⚠ 缺字段 {miss}：说明该模型对 JSON Schema 遵循不强，pipeline 里要多留一次重试。")
    else:
        print("  字段齐全，可以直接进入 D2。")
except Exception as e:
    print(f"  失败：{e}")
    print("  若不支持 response_format，改用提示词约束输出（在 prompt 里写"只输出 JSON"）即可，不必换模型。")

print("\n完成。把上面两段输出截图存进 docs/，是很好的开发过程留痕。")
