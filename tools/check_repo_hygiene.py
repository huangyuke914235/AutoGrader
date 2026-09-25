# -*- coding: utf-8 -*-
"""公开仓库前的自检：扫描**将要提交**的文件里有没有不该公开的东西。

为什么要有这个脚本：`.gitignore` 只能挡住"整个目录"，挡不住有人把密钥写进了
某个 .py、或者把带学号的样本塞进了 docs/。而这种事**不会报错** ——
推送上去的那一刻就已经泄露了，撤下来也晚了（Git 历史、镜像、搜索引擎都留得下）。

    python tools/check_repo_hygiene.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (名字, 正则, 说明)
PATTERNS = [
    ("密钥串", r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}",
     "API Key 一旦提交就视作已泄露：立刻去厂商后台吊销，不要只删文件"),
    ("疑似学号", r"(?<!\d)\d{10}(?!\d)",
     "10 位数字通常是学号 —— 公开仓库里不该有真实学生信息"),
    ("本机绝对路径", r"[A-Za-z]:\\Users\\|[A-Za-z]:/Users/",
     "暴露使用者用户名，也说明脚本写死了路径（云端会跑不起来）"),
    ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)",
     "个人信息，公开仓库里不该出现"),
]

# 示例 / 占位符：这些**不该**报警。
# 为什么必须做白名单：`.env.example` 里的 `sk-xxxxxxxx`、测试里的 `0123456789`
# 全是假数据。扫描器如果天天喊狼来了，真密钥混在里面时就会被当成噪音划过去 ——
# 那样这个脚本还不如不跑。
PLACEHOLDER = re.compile(
    r"^(?:[xX]+|[0]+|[1]+|[9]+|[aA]+|0123456789|1234567890"
    r"|sk-(?:[xX]+|abcdefghijklmnopqrst))$")
SAMPLE_FILES = (".env.example", ".example.json", "example.json")

# 扫描范围：只看 git 实际会提交的文件（尊重 .gitignore）
try:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
except Exception as e:                     # 没有 git 就退回全目录扫
    print(f"[warn] 读取 git 文件列表失败（{e}），改为扫描全部文件")
    files = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__", "data")]
        for f in fn:
            files.append(os.path.relpath(os.path.join(dp, f), ROOT))

if not files:
    print("没有找到待提交文件 —— 先 `git add -A`")
    sys.exit(1)

SKIP_EXT = (".png", ".jpg", ".pdf", ".pptx", ".docx", ".zip", ".mp4")
hits = 0
for rel in files:
    if rel.lower().endswith(SKIP_EXT):
        continue
    if any(rel.endswith(s) for s in SAMPLE_FILES):
        continue                       # 模板文件里本来就该写着假值
    path = os.path.join(ROOT, rel)
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    for name, pat, advice in PATTERNS:
        for m in re.finditer(pat, text):
            if PLACEHOLDER.match(m.group(0)):
                continue
            line = text[:m.start()].count("\n") + 1
            snippet = m.group(0)
            hits += 1
            print(f"[{name}] {rel}:{line}  ->  {snippet[:40]}")
            print(f"        {advice}")
            break                       # 每个文件每种类型只报第一处，避免刷屏

print()
if hits:
    print(f"发现 {hits} 处需要处理 —— 公开前请逐条确认。")
    sys.exit(1)
print(f"OK：{len(files)} 个待提交文件里没有发现密钥 / 学号 / 本机路径 / 手机号。")
