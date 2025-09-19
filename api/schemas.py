from __future__ import annotations
from typing import List, Optional, Union
from pydantic import BaseModel, Field

StrOrList = Union[str, List[str]]

class SearchRequest(BaseModel):
    # 兼容三种写法：keyword / keywords / kw
    keywords: Optional[StrOrList] = Field(default=None, description="关键词（字符串或字符串列表）")
    keyword: Optional[StrOrList] = Field(default=None, description="同 keywords 的别名")
    kw: Optional[StrOrList] = Field(default=None, description="同 keywords 的别名")

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    sources: Optional[List[str]] = None
    max_pages_per_source: int = 1

    def unified_keywords(self) -> str:
        """把 keyword / keywords / kw 统一为一个以空格分隔的字符串"""
        raw = self.keywords if self.keywords not in (None, "") else (
              self.keyword if self.keyword not in (None, "") else self.kw)
        if raw is None:
            return ""
        if isinstance(raw, list):
            parts = []
            for x in raw:
                if not x:
                    continue
                # 列表里可能也会带空格，拆开拼回
                parts.extend(str(x).strip().split())
            return " ".join([p for p in parts if p])
        # str
        return " ".join([p for p in str(raw).strip().split() if p])
