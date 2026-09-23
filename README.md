# AutoGrader · 实验报告智能评阅平台

> 粤港澳大湾区 AI Coding 创新大赛 · 方向一 AI + 教学管理助手
>
> **不做「让 AI 打个分」，而做「把评分标准变成可核查、可溯源、可校准的判定流水线」。**
> 分数是结果，每一个分数背后那句"证据在第 3 节，原文是……"才是产品价值。

## 团队

深圳大学计算机与软件学院 · 计算机科学与技术 · 2025 级 05 班，2 人：

- **黄宇科**（主程）：代码 / 部署 / 版本管理 / 自评测工具
- **张镒川**（产品与材料）：报告脱敏 / 评分点整理 / **独立人工打分（gold set）** / 试用 / PPT / 视频

> 分工里那条加粗的很重要：写 prompt 的人不能同时定义"正确答案"，
> 所以 gold set 由非主程成员独立打分、期间不讨论、完成后封存。
>
> **学号等身份信息一律不写入公开仓库**（在提交专区表单中填写）。

## 核心方法论：RAG-E 四阶判定链

```
R 评分点原子化   自然语言 rubric → 可独立判定的原子评分点（JSON）
A 证据锚定判定   逐评分点独立判定，必须回引报告原文
G 一致性守卫     低置信二次复核，不一致标记「待人工复核」
E 反馈生成       评语逐条绑定证据，附可执行改进建议
```

## 三条工程铁律

1. **分数由代码加总**，模型只输出单点判定（hit / partial / miss）
2. **无证据的判断不算数** —— 模型给出的每条引用，必须通过 Python 代码的原文逐字匹配校验，否则作废重跑；绝不放宽匹配规则让数字变好看
3. **不确定就交给人** —— 低置信或两次判定不一致，显式标记并置顶人工复核

## 实测数据

| 指标 | 结果 |
|---|---|
| 输出合法 JSON 率（同一输入 10 连测） | 100% |
| 证据原文精确匹配率 | 100% |
| 单个评分点判定耗时 | 约 1.5 秒 |
| 8 份真实报告得分区分度 | 22 – 73 分（极差 51） |
| 单份报告端到端（8 个评分点 + 评语） | 约 50 秒 |

诚实记录：同一输入 10 次中有 2 次判定在 hit / partial 间漂移——这正是 G 阶段存在的理由。

> **项目定位**：这是竞赛原型 / 教师辅助初评工具，**不是**可以无人监督直接发布正式成绩的自动评分系统。
> 系统只产出带证据的初评，最终成绩由教师确认；不支持图片与截图内容（无 OCR）。

## 快速开始（干净环境从零安装）

```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash；PowerShell 用 .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 仅跑测试时需要
cp .env.example .env                 # 然后填入自己的 key（.env 不提交）
python tools/test_key.py             # 验证密钥与结构化输出是否可用
python -m pytest tests/ -q           # 无 key 也能跑，全部通过才算干净
streamlit run app.py                 # 打开 http://localhost:8501
```

`.env` 内容（也可直接复制 `.env.example`）：

```
LLM_API_KEY=sk-xxxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
DEMO_MODE=false
```

`DEMO_MODE=true` 时**真的不会**调用模型（代码层拦截，不只是界面提示）；
需要离线演示请先 `python tools/make_demo.py` 生成演示结果。

## 自动化测试

```bash
python -m pytest tests/ -q
```

- 48 项，全部使用 mock，**不需要 API key、不联网**。
- 覆盖：数据契约与 rubric 校验、证据校验与降级分支、一致性守卫（低置信/复核失败/不一致/分差）、
  人工改分与导出口径、解析器偏移与解析体检、隐私与 CSV 注入、提示词注入检测。
- 依赖真实模型的评测请跑 `tools/batch_run.py` + `tools/benchmark.py`（会花钱，结果版本化保存）。

启动：

```bash
streamlit run app.py
```

打开浏览器访问 http://localhost:8501 即可使用。

## 目录结构

```
├── app.py            # Streamlit 界面主入口（五个标签页的骨架）
├── batch_ui.py       # 第 ⑤ 个标签页「批量测试」（批量评阅 / 一致性重复测试）
├── models.py         # Pydantic 数据契约
├── prompts.py        # RAG-E 四阶段 Prompt 模板
├── llm.py            # 模型调用 + JSON 宽容解析 + 防幻觉闸门
├── parser.py         # 报告解析与章节切分（纯确定性代码，不调模型）
├── pipeline.py       # 四阶流水线编排
├── tests/            # pytest 测试（mock，无 key 可跑）
├── docs/             # GitHub Pages 作品主页 + 离线可看案例 + 三篇故障排查记录
└── tools/            # 脱敏 / 连通性自检 / 稳定性测试 / gold 打分表 / 批量评测
```

## 批量测试（界面第 ⑤ 个标签页）

两种批量能力，都可以导出 CSV 留痕：

- **多份报告批量评阅**：一次跑完选中的报告，出横向对比表；
  本地存在封存的人工 `gold.json` 时，自动对齐算出 MAE / 误差≤5 分占比 / 证据可溯源率。
- **同一份报告重复评阅**：连跑 N 次，看总分极差与评分点判定稳定率 ——
  这是「G 一致性守卫」存在的实证，稳不稳用数据说话。

> 注：自定义评分点与人工 gold 的维度对不上，此时界面会**拒绝计算 MAE**，
> 因为维度不一致时算出来的误差没有意义。

## 故障排查记录

- [docs/bugfix-上下文丢失.md](docs/bugfix-上下文丢失.md) —— 模型只看到报告 7%~11% 内容（MAE 31.67→20.44）
- [docs/bugfix-反馈生成失败.md](docs/bugfix-反馈生成失败.md) —— 异常被吞 + 模型输出 JSON 不合法（修后仍偶发，已如实记录）
- [docs/bugfix-引用匹配与PDF断行.md](docs/bugfix-引用匹配与PDF断行.md) —— 引用匹配不上原文，11 个评分点被误判 0 分（MAE 20.44→7.56）
- [视频分镜脚本](docs/视频分镜脚本.md) / [社媒过程帖与提交自查](docs/社媒过程帖与提交自查.md)

## 隐私与数据说明

- 本仓库**不包含**任何原始实验报告。`data/` 目录已在 `.gitignore` 中，原始文件只保留在本地。
- 主页 `docs/cases/` 中的演示案例已做双重处理：**报告全文脱敏**（学号 / 姓名 / 班级 / 电话 / 邮箱置零残留）+ **正文裁剪为证据片段窗口**（每条证据 ±500 字），不含完整作业原文。
- 脱敏脚本 `tools/anonymize.py` **不内置任何真实路径与姓名**：样本清单从 `tools/sources.local.json` 读取，
  该文件已在 `.gitignore` 中（模板见 `tools/sources.local.example.json`）。
  这是 2026-09-22 自查发现并修复的一次真实隐私事故——**Git 历史里仍留有旧版本，需要另行清理**（见下文）。
- 上传的文件写入受控临时目录（`data/tmp_uploads/`，UUID 文件名），解析结束即在 `finally` 中删除；
  `report_id` 为随机值，不参与路径拼接。

> ⚠ **需要人工处理**：仓库早期提交里曾把真实姓名/学号写进 `tools/anonymize.py`。
> 当前受跟踪文件已清除，但 **Git 历史中仍然存在**，需要时用 `git filter-repo` / BFG 清理并强制推送。

## 第三方开源库

| 库 | 用途 |
|---|---|
| [Streamlit](https://streamlit.io) | Web 界面（Apache-2.0） |
| [PyMuPDF](https://pymupdf.readthedocs.io) | PDF 文本提取（AGPL） |
| [python-docx](https://python-docx.readthedocs.io) | Word 文档解析（MIT） |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | 调用 OpenAI 兼容接口（本作品接 DeepSeek）（Apache-2.0） |
| [Pydantic](https://docs.pydantic.dev) | 数据校验与 JSON Schema 约束（MIT） |
| [pandas](https://pandas.pydata.org) | 结果表格与 CSV 导出（BSD） |

## 许可证

[MIT](LICENSE)
