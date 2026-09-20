# AutoGrader · 实验报告智能评阅平台

> 粤港澳大湾区 AI Coding 创新大赛 · 方向一 AI + 教学管理助手
>
> **不做「让 AI 打个分」，而做「把评分标准变成可核查、可溯源、可校准的判定流水线」。**
> 分数是结果，每一个分数背后那句"证据在第 3 节，原文是……"才是产品价值。

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

## 快速开始

```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash
pip install -r requirements.txt
```

在根目录建 `.env`（不要提交）：

```
LLM_API_KEY=sk-xxxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

启动：

```bash
streamlit run app.py
```

打开浏览器访问 http://localhost:8501 即可使用。

## 目录结构

```
├── app.py            # Streamlit 界面（评阅 / 详情对照 / 评分点 / 导出）
├── models.py         # Pydantic 数据契约
├── prompts.py        # RAG-E 四阶段 Prompt 模板
├── llm.py            # 模型调用 + JSON 校验 + 防幻觉闸门
├── parser.py         # 报告解析与章节切分（纯确定性代码，不调模型）
├── pipeline.py       # 四阶流水线编排
├── docs/             # GitHub Pages 作品主页 + 离线可看案例
└── tools/            # 脱敏 / 连通性自检 / 稳定性测试 / 批量评测
```

## 隐私与数据说明

- 本仓库**不包含**任何原始实验报告。`data/` 目录已在 `.gitignore` 中，原始文件只保留在本地。
- 主页 `docs/cases/` 中的演示案例已做双重处理：**报告全文脱敏**（学号 / 姓名 / 班级 / 电话 / 邮箱置零残留）+ **正文裁剪为证据片段窗口**（每条证据 ±500 字），不含完整作业原文。

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
