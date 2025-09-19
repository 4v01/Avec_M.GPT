# -*- coding: utf-8 -*-
from __future__ import annotations
from pydantic import BaseModel, Field, validator
from typing import Optional

class CrawlReq(BaseModel):
    keywords: str = Field("", description="搜索关键词，支持中文", min_length=1, max_length=200)
    start: Optional[str] = Field(None, description="YYYY-MM-DD 或 YYYY/MM/DD")
    end: Optional[str] = Field(None, description="YYYY-MM-DD 或 YYYY/MM/DD")
    chinaso: bool = Field(True, description="是否启用中国搜索")
    epaper: bool = Field(True, description="是否启用电子报")
    portal: bool = Field(True, description="是否启用门户网站")

    @validator('keywords')
    def validate_keywords(cls, v):
        if not v or not v.strip():
            raise ValueError('关键词不能为空')
        return ' '.join(v.strip().split())

class DailyReq(BaseModel):
    hours: int = Field(24, ge=1, le=168, description="回溯小时数（1-168）")
    use_topics: bool = Field(True, description="读取 config/daily_topics.json")

class ProgressResponse(BaseModel):
    phase: str = Field("idle", description="当前阶段")
    step: int = Field(0, description="当前步骤")
    total: int = Field(0, description="总步骤数")
    percent: int = Field(0, description="完成百分比")
    message: str = Field("ready", description="状态消息")
    log: Optional[str] = Field(None, description="最新日志")
    updated_at: str = Field("", description="更新时间")
