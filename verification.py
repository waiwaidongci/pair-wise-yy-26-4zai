"""逐版本修复核对的判定规则。

本模块只包含纯函数：不接触数据库与网络，输入普通字典/列表，
输出每版进度、阻塞项与可否推进已解决的结论。
存储见 database.py，页面展示见 static/index.html。
"""
from __future__ import annotations

import hashlib
import json

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULTS = (RESULT_PASS, RESULT_FAIL)

STATE_PENDING = "pending"


def scope_fingerprint(summary: str, versions: list[str], plan: str, target_date: str | None) -> str:
    """摘要、受影响版本与修复计划的指纹；任一改动都会使既有核对结论作废。"""
    payload = {
        "summary": summary.strip(),
        "versions": sorted(str(v).strip() for v in versions),
        "plan": plan.strip(),
        "target_date": (target_date or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_current(check: dict, scope_hash: str) -> bool:
    """核对结论是否仍对应当前内容（未作废）。"""
    return check.get("scope_hash") == scope_hash


def latest_per_version(checks: list[dict], scope_hash: str) -> dict[str, dict]:
    """当前内容下每个版本最近一次核对结论（checks 需按时间升序）。"""
    latest: dict[str, dict] = {}
    for check in checks:
        if is_current(check, scope_hash):
            latest[check["version_key"]] = check
    return latest


def version_progress(versions: list[str], checks: list[dict], scope_hash: str) -> list[dict]:
    """每个受影响版本的进度：pass/fail/pending、最近结论与已作废条数。"""
    latest = latest_per_version(checks, scope_hash)
    progress = []
    for version in versions:
        check = latest.get(version)
        superseded = sum(1 for c in checks if c["version_key"] == version and not is_current(c, scope_hash))
        progress.append({
            "version_key": version,
            "state": check["result"] if check else STATE_PENDING,
            "check": check,
            "superseded": superseded,
        })
    return progress


def failed_versions(progress: list[dict]) -> list[str]:
    return [p["version_key"] for p in progress if p["state"] == RESULT_FAIL]


def pending_versions(progress: list[dict]) -> list[str]:
    return [p["version_key"] for p in progress if p["state"] == STATE_PENDING]


def blocking_versions(progress: list[dict]) -> list[str]:
    """仍未通过的版本（失败或待复测），阻塞推进已解决。"""
    return [p["version_key"] for p in progress if p["state"] != RESULT_PASS]


def can_resolve(progress: list[dict]) -> bool:
    return bool(progress) and not blocking_versions(progress)
