# -*- coding: utf-8 -*-
"""数据结构定义（全项目的数据契约）

三条硬约束在这里落成代码，而不是只写在文档里：
1. 总分由代码加总 —— ItemJudgement 里**没有** total 字段。
2. miss 必须 0 分且无证据；非 miss 必须至少有一条通过原文逐字校验的证据。
3. 模型输出的 score 受 verdict 约束，矛盾会被标记（见 llm.normalize_judgement）。
"""
import math
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

VERDICTS = ("hit", "partial", "miss")
MAX_RUBRIC_ITEMS = 12
QUOTE_MIN_LEN = 6
QUOTE_MAX_LEN = 200          # 上限：防止整段照搬，也防止上下文被一条引用吃掉


class Section(BaseModel):
    """报告的一个章节/片段"""
    id: str
    title: str
    level: int = 1
    text: str
    char_start: int = 0
    char_end: int = 0


class RubricItem(BaseModel):
    """原子评分点（由自然语言 rubric 拆解而来）"""
    id: str
    name: str
    criteria: str
    max_score: float
    positive_signals: List[str] = Field(default_factory=list)
    negative_signals: List[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("评分点 id 不能为空")
        return v

    @field_validator("max_score")
    @classmethod
    def _score_finite_positive(cls, v: float) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError("max_score 必须是数字")
        if math.isnan(v) or math.isinf(v):
            raise ValueError("max_score 不能是 NaN / Infinity")
        if v <= 0:
            raise ValueError(f"max_score 必须是正数（收到 {v}）")
        return v


class Rubric(BaseModel):
    """一份完整的评分标准"""
    items: List[RubricItem]
    source_hash: str = ""            # 生成时的「评分标准原文 + 课程背景」哈希，用于判断过期


class Evidence(BaseModel):
    """判定依据：必须是报告原文中逐字存在的片段"""
    section_id: str = ""
    quote: str = Field(description="从报告中逐字复制的原文片段")
    char_start: int = -1


class ItemJudgement(BaseModel):
    """单个评分点的判定结果（模型唯一被允许输出的东西）"""
    # 模型不需要输出下面这些字段（pipeline 判定完回填），所以都设默认值
    rubric_item_id: str = ""
    verdict: str = Field(description="只能是 hit / partial / miss 之一")
    score: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    evidence: List[Evidence] = Field(default_factory=list)
    needs_review: bool = False
    # 被原文校验剔除的引用：留着是为了让「可溯源率」仍按**模型原始产出**计算，
    # 而不是按筛选后的幸存者计算（后者会让这个数字虚高，属于自欺）
    dropped_quotes: List[str] = Field(default_factory=list)
    # 系统错误（调用失败/解析失败）与「学生没做到」是两回事，
    # 绝不能把前者当成失分算到学生头上
    system_error: str = ""


class HumanOverride(BaseModel):
    """教师人工改分记录（留痕用）"""
    rubric_item_id: str
    original_score: float
    new_score: float
    reason: str
    created_at: str = ""


class Feedback(BaseModel):
    """面向学生的评语"""
    summary: str = ""
    per_item: dict = Field(default_factory=dict)
    suggestions: List[str] = Field(default_factory=list)
    # 以下字段由代码填写，不要求模型输出（有默认值，不影响校验）
    generated_by: str = "model"      # model=模型生成 / fallback=规则兜底
    error: str = ""                  # E 阶段失败时的真实原因，不允许再被静默吞掉


class OcrImageNote(BaseModel):
    """多模态 OCR 从页面图片里读出的一处非文字内容（截图/表格/图表/公式）。

    它是**旁证**，不是学生写的正文：提示词明确要求「读不出来就写无法辨认」，
    因此它参与判定时也必须能被原文逐字校验，走的是与正文完全相同的证据闸门。
    """
    kind: str = "other"        # screenshot / code / table / chart / diagram / formula / other
    caption: str = ""
    content: str = ""


class OcrPage(BaseModel):
    """一页的多模态转录结果。页码是证据坐标，必须保留。"""
    page: int = 0
    text: str = ""
    images: List[OcrImageNote] = Field(default_factory=list)
    legible: bool = True
    notes: str = ""
    chars: int = 0
    cached: bool = False
    error: str = ""


class RunInfo(BaseModel):
    """一次评分的可审计元信息（用于事后复现，不参与打分）"""
    created_at: str = ""
    model: str = ""
    base_url: str = ""
    temperature: float = 0.0
    rubric_source_hash: str = ""     # 生成 rubric 时的「评分标准 + 课程背景」哈希
    rubric_raw: str = ""             # 评分标准原文
    report_hash: str = ""            # 报告正文哈希
    report_chars: int = 0
    parse_warnings: List[str] = Field(default_factory=list)
    parse_coverage: str = ""         # full / retrieved
    enable_recheck: bool = False
    prompt_version: str = ""         # prompts.py 的内容哈希
    calls: int = 0
    tokens: int = 0
    failed_calls: int = 0
    json_repaired: int = 0
    system_errors: int = 0           # 本次有几个评分点因系统错误未判定
    injection_hits: List[str] = Field(default_factory=list)   # 命中的评分操纵指令特征
    # 多模态 OCR（图片/截图内容）—— 不开启时全部为 0/False，界面据此如实显示
    ocr_used: bool = False
    ocr_model: str = ""
    ocr_pages: int = 0               # 实际读了多少页
    ocr_chars: int = 0               # 从图片里读出的字符数
    ocr_images: int = 0              # 识别出的图片/表格/图表处数
    ocr_failed: int = 0              # 读失败的页数（逐页隔离，不等于整份失败）
    ocr_cached_hits: int = 0         # 命中缓存的页数（不重复计费）


class SelfCheckIssue(BaseModel):
    """体检发现的一个问题。

    direction 是**改进方向**，不是可抄录的答案：长度上限由 selfcheck 在代码层强制截断。
    """
    problem: str = ""
    location: str = ""
    impact: str = "中"                # 较小 / 中 / 较大
    direction: str = ""


class SelfCheckItem(BaseModel):
    """一个检查项的结果"""
    name: str = ""
    score: float = 0.0                # 0-100
    status: str = "偏弱"               # 通过 / 偏弱 / 缺失 / 未检测
    engine: str = "rule"              # rule=规则引擎 / ai=模型
    issues: List[SelfCheckIssue] = Field(default_factory=list)


class SelfCheckPayload(BaseModel):
    """模型在自检阶段**唯一**被允许输出的东西（没有 total，权重也不归它管）"""
    items: List[SelfCheckItem] = Field(default_factory=list)


class SelfCheckResult(BaseModel):
    """一次自检的结果（total 由代码加总，与 GradingResult 同一条纪律）"""
    report_id: str = ""
    experiment_type: str = ""
    items: List[SelfCheckItem] = Field(default_factory=list)
    total: float = 0.0
    # 未检测项（AI 不可用）不计入总分，这里如实记录，界面要说清楚
    detected_count: int = 0
    undetected: List[str] = Field(default_factory=list)
    use_ai: bool = False
    model: str = ""
    created_at: str = ""
    error: str = ""                   # 自检失败的真实原因，不允许静默吞掉


class GradingResult(BaseModel):
    """一份报告的最终评阅结果（total 由代码计算）"""
    report_id: str = ""
    items: List[RubricItem] = Field(default_factory=list)
    judgements: List[ItemJudgement] = Field(default_factory=list)
    total: float = 0.0
    ai_total: float = 0.0            # AI 原始总分（人工改分后仍保留，便于对照）
    # 有评分点因系统错误未判定时，这个总分是不完整的，不能拿去算 MAE
    total_incomplete: bool = False
    feedback: Optional[Feedback] = None
    model: str = ""
    elapsed_sec: float = 0.0
    overrides: List[HumanOverride] = Field(default_factory=list)
    run_info: Optional[RunInfo] = None
    # 多模态 OCR 的本次运行明细（页面转录、图片识别、失败页），界面与导出据此展示。
    # 它不是分数的一部分：OCR 只影响「模型能看到什么」，不影响怎么算分。
    ocr: Optional[dict] = None
    # 预置演示结果的元信息（仅 Demo 模式填充：来源、生成时间、引擎等）
    demo_meta: Optional[dict] = None
