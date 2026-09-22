# -*- coding: utf-8 -*-
"""数据结构定义（全项目的数据契约）

纪律：总分由代码加总，模型只输出单点判定。因此 ItemJudgement 里**没有** total 字段。
"""
from typing import List, Optional
from pydantic import BaseModel, Field


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


class Rubric(BaseModel):
    """一份完整的评分标准"""
    items: List[RubricItem]


class Evidence(BaseModel):
    """判定依据：必须是报告原文中逐字存在的片段"""
    section_id: str = ""
    quote: str = Field(description="从报告中逐字复制的原文片段，不超过 60 字")
    char_start: int = -1


class ItemJudgement(BaseModel):
    """单个评分点的判定结果（模型唯一被允许输出的东西）"""
    # 注意：模型不需要输出这个字段（pipeline 判定完再回填），所以设为可选
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


class Feedback(BaseModel):
    """面向学生的评语"""
    summary: str = ""
    per_item: dict = Field(default_factory=dict)
    suggestions: List[str] = Field(default_factory=list)
    # 以下两个字段由代码填写，不要求模型输出（有默认值，不影响校验）
    generated_by: str = "model"      # model=模型生成 / fallback=规则兜底
    error: str = ""                  # E 阶段失败时的真实原因，不允许再被静默吞掉


class GradingResult(BaseModel):
    """一份报告的最终评阅结果（total 由代码计算）"""
    report_id: str = ""
    items: List[RubricItem] = Field(default_factory=list)
    judgements: List[ItemJudgement] = Field(default_factory=list)
    total: float = 0.0
    feedback: Optional[Feedback] = None
    model: str = ""
    elapsed_sec: float = 0.0
