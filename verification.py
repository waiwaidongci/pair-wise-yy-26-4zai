"""逐版本修复核对——判定层。

职责划分：
- 存储：database.py 中的 version_fixes 表、reports.fix_revision 及存取原语；
- 判定：本模块（进度汇总、解决门禁、结论作废规则）；
- 页面/接口：app.py 与 static/index.html。

规则：
- 维护者按受影响版本逐条登记处理说明、结果(pass/fail)和时间；
- 任一版本失败或未登记，报告保持 fixing，并列出失败/待复测版本；
- 全部版本在当前轮次通过后，协调员才能推进到 resolved；
- 同一版本再次复测只追加记录，旧结论保留为历史，以最新结果为准；
- 摘要、受影响版本或修复计划改动后 fix_revision +1，旧轮次结论一律作废，需按新内容重新登记。
"""
from __future__ import annotations

from datetime import datetime

from database import DomainError

PASS = "pass"
FAIL = "fail"
RESULTS = {PASS, FAIL}
VOID_REASON = "原有结论已作废，需按新内容重新登记"


def _lite(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "version": row["version_key"],
        "maintainer_id": row["maintainer_id"],
        "note": row["note"],
        "result": row["result"],
        "tested_at": row["tested_at"],
        "revision": row["revision"],
    }


def evaluate(version_keys: list[str], rows: list[dict], revision: int) -> dict:
    """纯判定：根据当前版本清单、全部核对记录和当前轮次，汇总每版进度与阻塞项。"""
    grouped: dict[str, list[dict]] = {v: [] for v in version_keys}
    for row in rows:
        grouped.setdefault(row["version_key"], []).append(row)

    items: list[dict] = []
    failed: list[str] = []
    pending: list[str] = []
    for version in version_keys:
        vrows = sorted(grouped.get(version, []), key=lambda r: r["id"])
        current = [r for r in vrows if r["revision"] == revision]
        voided = [r for r in vrows if r["revision"] < revision]
        latest = current[-1] if current else None
        state = latest["result"] if latest else "pending"
        blocker = None
        if state == FAIL:
            blocker = "复测失败"
            failed.append(version)
        elif latest is None:
            blocker = VOID_REASON if voided else "尚未登记复测结论"
            pending.append(version)
        items.append({
            "version": version,
            "state": state,  # pass / fail / pending
            "latest": _lite(latest),
            "voided_latest": _lite(voided[-1]) if voided else None,
            "attempts": len(current),
            "voided_attempts": len(voided),
            "history": [_lite(r) for r in vrows],  # 再次复测保留旧结论
            "blocker": blocker,
        })

    blockers = [{"version": it["version"], "reason": it["blocker"]} for it in items if it["blocker"]]
    return {
        "revision": revision,
        "all_passed": bool(version_keys) and not blockers,
        "failed_versions": failed,
        "pending_versions": pending,
        "blockers": blockers,
        "versions": items,
    }


class FixVerification:
    """对外的判定服务：登记核对、修订作废、进度查询、解决门禁。"""

    def __init__(self, db) -> None:
        self.db = db

    def _report(self, report_id: int):
        report = self.db.conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            raise DomainError("报告不存在")
        return report

    @staticmethod
    def _parse_time(value) -> str:
        if value is None or not str(value).strip():
            return datetime.now().isoformat(timespec="seconds")
        text = str(value).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                datetime.strptime(text, fmt)
                return text
            except ValueError:
                continue
        raise DomainError("复测时间必须使用 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS")

    def record(self, report_id: int, version_key: str, maintainer_id: int,
               note: str, result: str, tested_at=None) -> int:
        """维护者为单个受影响版本登记处理说明、结果和时间（追加，不覆盖旧结论）。"""
        user = self.db._user(maintainer_id)
        report = self._report(report_id)
        if user["role"] != "maintainer" or not self.db._member(report_id, maintainer_id):
            raise DomainError("只有该报告的维护者可以登记逐版本修复核对")
        if report["status"] != "fixing":
            raise DomainError("只有修复中的报告可以登记版本核对")
        key = str(version_key).strip()
        if not key:
            raise DomainError("版本号不能为空")
        keys = [r["version_key"] for r in self.db.fetch_affected_versions(report_id)]
        if key not in keys:
            raise DomainError(f"版本 {key} 不在受影响版本清单中")
        if result not in RESULTS:
            raise DomainError("核对结果必须是 pass 或 fail")
        if not str(note).strip():
            raise DomainError("处理说明不能为空")
        when = self._parse_time(tested_at)
        clean_note = str(note).strip()

        with self.db.transaction():
            revision = self.db.conn.execute(
                "SELECT fix_revision FROM reports WHERE id=?", (report_id,)
            ).fetchone()["fix_revision"]
            fix_id = self.db.insert_version_fix(
                report_id, key, maintainer_id, clean_note, result, when, revision
            )
            word = "通过" if result == PASS else "失败"
            for coord in self.db.conn.execute(
                "SELECT user_id FROM report_members WHERE report_id=? AND member_role='coordinator'",
                (report_id,),
            ):
                self.db._notify(
                    report_id, coord["user_id"], "version_fix",
                    f"版本 {key} 复测{word}：{clean_note}",
                )
        return fix_id

    def revise(self, report_id: int, user_id: int, summary=None, versions=None) -> dict:
        """修改摘要或受影响版本；内容实际变化时旧核对结论整批作废。"""
        user = self.db._user(user_id)
        report = self._report(report_id)
        if not (user["role"] == "coordinator" or user_id == report["reporter_id"]):
            raise DomainError("只有协调员或报告人可以修改摘要或受影响版本")
        if report["status"] == "published":
            raise DomainError("已披露报告不能修改")
        if report["status"] not in {"new", "triaged", "fixing"}:
            raise DomainError("报告已离开修复阶段，不能再改摘要或受影响版本；如需调整请先由协调员退回修复中")

        new_summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
        normalized: list[str] | None = None
        if versions is not None:
            if not isinstance(versions, list):
                raise DomainError("受影响版本必须是数组")
            normalized = []
            for version in versions:
                key = str(version).strip()
                if not key:
                    raise DomainError("版本号不能为空")
                if key in normalized:
                    raise DomainError(f"版本 {key} 重复")
                normalized.append(key)
            if not normalized:
                raise DomainError("受影响版本不能为空")

        changed: list[str] = []
        with self.db.transaction():
            if new_summary is not None and new_summary != report["summary"]:
                self.db.conn.execute(
                    "UPDATE reports SET summary=? WHERE id=?", (new_summary, report_id)
                )
                changed.append("summary")
            if normalized is not None:
                old_keys = [r["version_key"] for r in self.db.fetch_affected_versions(report_id)]
                if set(normalized) != set(old_keys):
                    self.db.conn.execute(
                        f"DELETE FROM affected_versions WHERE report_id=? AND version_key NOT IN "
                        f"({','.join('?' * len(normalized))})",
                        [report_id, *normalized],
                    )
                    for key in normalized:
                        if key not in old_keys:
                            self.db.conn.execute(
                                "INSERT INTO affected_versions(report_id,version_key,details) VALUES(?,?,?)",
                                (report_id, key, ""),
                            )
                    changed.append("versions")
            if changed:
                self.db.conn.execute(
                    "UPDATE reports SET fix_revision=fix_revision+1,updated_at=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), report_id),
                )
                labels = "、".join("摘要" if c == "summary" else "受影响版本" for c in changed)
                for member in self.db.conn.execute(
                    "SELECT user_id FROM report_members WHERE report_id=?", (report_id,)
                ):
                    self.db._notify(
                        report_id, member["user_id"], "fix_void",
                        f"{labels}已变更，逐版本修复核对结论全部作废，需按新内容重新登记",
                    )
            revision = self.db.conn.execute(
                "SELECT fix_revision FROM reports WHERE id=?", (report_id,)
            ).fetchone()["fix_revision"]
        return {"changed": changed, "revision": revision}

    def progress(self, report_id: int, user_id: int | None = None) -> dict:
        report = self._report(report_id)
        if user_id is not None and not self.db.can_view(report_id, user_id):
            raise DomainError("无权查看该漏洞报告")
        keys = [r["version_key"] for r in self.db.fetch_affected_versions(report_id)]
        rows = self.db.fetch_version_fixes(report_id)
        return evaluate(keys, rows, report["fix_revision"])

    def assert_resolvable(self, report_id: int) -> None:
        """解决门禁：有失败项或未完成核对时报错，报告因此留在修复中。"""
        summary = self.progress(report_id)
        if summary["all_passed"]:
            return
        parts: list[str] = []
        if summary["failed_versions"]:
            parts.append("失败版本：" + "、".join(summary["failed_versions"]))
        voided = [b["version"] for b in summary["blockers"] if b["reason"] == VOID_REASON]
        if voided:
            parts.append("结论已作废需重新登记：" + "、".join(voided))
        fresh_pending = [v for v in summary["pending_versions"] if v not in voided]
        if fresh_pending:
            parts.append("尚未复测：" + "、".join(fresh_pending))
        raise DomainError("逐版本修复核对未全部通过，报告保持修复中；" + "；".join(parts))
