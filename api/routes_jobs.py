# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .services.scrapyd_runner import run_all, list_spiders as scrapyd_list_spiders

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

JOB_DIR = os.path.join("var", "jobs")
os.makedirs(JOB_DIR, exist_ok=True)

def _job_file(job_id: str) -> str:
    return os.path.join(JOB_DIR, f"{job_id}.json")


class JobRequest(BaseModel):
    keywords: Optional[List[str] | str] = Field(default="")
    start: Optional[str] = Field(default="")
    end: Optional[str] = Field(default="")
    last_hours: Optional[int] = Field(default=0)
    max_pages: Optional[int] = Field(default=1)
    enable_mobile: Optional[int | bool | str] = Field(default=1)
    enable_pc: Optional[int | bool | str] = Field(default=1)
    spiders: Optional[List[str]] = None
    project: Optional[str] = None

class JobResponse(BaseModel):
    job_id: str
    project: str
    scrapyd_url: str
    keywords: List[str]
    spiders: List[Dict[str, Any]]


@router.get("/spiders", summary="列出可用 spiders")
def list_spiders(project: Optional[str] = None) -> Dict[str, Any]:
    sp = scrapyd_list_spiders(project or os.environ.get("SCRAPYD_PROJECT", "pr_crawler"))
    return {"spiders": sp}


@router.post("/search", response_model=JobResponse, summary="批量下发搜索类爬虫")
async def create_search_job(payload: JobRequest):
    job_id = time.strftime("%Y%m%d-%H%M%S")
    result = run_all(job_id, payload.dict(exclude_none=True))

    # 落盘一份（日志/调试用，不影响主流程）
    try:
        with open(_job_file(job_id), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return JobResponse(**{
        "job_id": result["job_id"],
        "project": result["project"],
        "scrapyd_url": result["scrapyd_url"],
        "keywords": result.get("keywords", []),
        "spiders": result.get("spiders", []),
    })
