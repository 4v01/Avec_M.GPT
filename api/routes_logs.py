# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api", tags=["logs"])

SCRAPYD_URL = os.environ.get("SCRAPYD_URL", "http://127.0.0.1:6800")
JOB_DIR = os.path.join("var", "jobs")

def _job_file(job_id: str) -> str:
    return os.path.join(JOB_DIR, f"{job_id}.json")

def _read_job(job_id: str) -> Dict[str, Any]:
    fn = _job_file(job_id)
    if not os.path.exists(fn):
        raise FileNotFoundError(f"job meta not found: {fn}")
    with open(fn, "r", encoding="utf-8") as f:
        return json.load(f)

@router.get("/debug/job-log", summary="按 job_id 聚合拉取各 spider 的 log")
async def read_job_log(job_id: str = Query(..., description="形如 20250909-095536")):
    try:
        meta = _read_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")

    project = meta.get("project") or "pr_crawler"
    logs: Dict[str, str] = {}

    # scrapyd 的标准日志路径：/logs/{project}/{spider}/{jobid}.log
    async with httpx.AsyncClient(base_url=SCRAPYD_URL, timeout=20.0) as c:
        for entry in meta.get("spiders", []):
            sp = entry.get("spider")
            jid = entry.get("scrapyd_jobid")
            if not sp or not jid:
                continue
            url = f"/logs/{project}/{sp}/{jid}.log"
            try:
                r = await c.get(url)
                if r.status_code == 200:
                    logs[sp] = r.text
                else:
                    logs[sp] = f"[{r.status_code}] {r.text[:400]}"
            except Exception as e:
                logs[sp] = f"[error] {e}"

    return {
        "job_id": job_id,
        "project": project,
        "scrapyd_url": SCRAPYD_URL,
        "logs": logs,
    }
