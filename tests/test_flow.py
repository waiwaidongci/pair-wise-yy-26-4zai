import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import DomainError, VulnerabilityDB
from verification import FixVerification, VOID_REASON

class VulnerabilityFlowTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=VulnerabilityDB(self.path)
        self.reporter=self.db.add_user("报告人","reporter","研究所"); self.coord=self.db.add_user("协调员","coordinator","响应中心"); self.maint=self.db.add_user("维护者","maintainer","项目组"); self.outsider=self.db.add_user("旁观者","reporter","外部")
        self.product=self.db.add_product("网关","项目组")
        self.report=self.db.create_report("鉴权绕过",self.product,self.reporter,"特制请求可绕过鉴权","2026-10-30",["3.2.0"])
        self.checks=FixVerification(self.db)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def _to_fixing(self):
        self.db.add_member(self.report,self.maint,"maintainer",self.coord)
        self.db.set_status(self.report,"triaged",self.coord)
        self.db.set_status(self.report,"fixing",self.coord)
        self.db.set_fix_plan(self.report,self.maint,"增加鉴权前置校验", "2026-10-20")
    def _advance_to_resolved(self):
        self._to_fixing()
        self.checks.record(self.report,"3.2.0",self.maint,"已打补丁并回归","pass","2026-10-18T10:00:00")
        self.db.set_status(self.report,"resolved",self.coord)
        self.db.create_advisory_draft(self.report,"受影响版本 3.2.0。请升级到 3.2.1。",self.coord)
    def test_full_disclosure_flow_and_early_publish_rejected(self):
        self._advance_to_resolved()
        with self.assertRaisesRegex(DomainError,"提前披露"):
            self.db.publish_report(self.report,self.coord,"2026-10-01")
        self.db.publish_report(self.report,self.coord,"2026-10-30")
        advisory=self.db.get_advisory(self.report,self.outsider)
        self.assertEqual("published",advisory["status"])
        self.assertTrue(self.db.notifications_for(self.maint))
    def test_denies_outsider_and_duplicate_report(self):
        with self.assertRaisesRegex(DomainError,"无权"):
            self.db.get_report_for_user(self.report,self.outsider)
        with self.assertRaisesRegex(DomainError,"重复"):
            self.db.create_report("重复问题",self.product,self.reporter,"相同版本的另一份报告","2026-11-01",["3.2.0"])
        self.db.add_member(self.report,self.maint,"maintainer",self.coord)
        self.db.add_evidence(self.report,"协调材料","secret","coordinator",self.coord)
        visible=self.db.get_report_for_user(self.report,self.maint)
        self.assertEqual([],visible["evidence"])

class PerVersionFixTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=VulnerabilityDB(self.path)
        self.reporter=self.db.add_user("报告人","reporter","研究所"); self.coord=self.db.add_user("协调员","coordinator","响应中心"); self.maint=self.db.add_user("维护者","maintainer","项目组"); self.outsider=self.db.add_user("旁观者","maintainer","外部")
        self.product=self.db.add_product("网关","项目组")
        self.report=self.db.create_report("鉴权绕过",self.product,self.reporter,"特制请求可绕过鉴权","2026-11-30",["3.1.0","3.2.0","3.3.0"])
        self.checks=FixVerification(self.db)
        self.db.add_member(self.report,self.coord,"coordinator",self.coord)
        self.db.add_member(self.report,self.maint,"maintainer",self.coord)
        self.db.set_status(self.report,"triaged",self.coord)
        self.db.set_status(self.report,"fixing",self.coord)
        self.db.set_fix_plan(self.report,self.maint,"三版本统一前置校验", "2026-11-20")
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def _err(self, fn, *args):
        try: fn(*args)
        except DomainError as exc: return str(exc)
        self.fail("应当抛出 DomainError")
    def test_failure_keeps_fixing_and_lists_failed_versions(self):
        self.checks.record(self.report,"3.1.0",self.maint,"补丁就绪","pass","2026-11-10")
        self.checks.record(self.report,"3.2.0",self.maint,"仍可绕过","fail","2026-11-11")
        # 3.3.0 未登记
        err=self._err(self.db.set_status,self.report,"resolved",self.coord)
        self.assertIn("保持修复中",err); self.assertIn("3.2.0",err); self.assertIn("3.3.0",err)
        progress=self.checks.progress(self.report,self.coord)
        self.assertFalse(progress["all_passed"])
        self.assertEqual(["3.2.0"],progress["failed_versions"])
        self.assertEqual({"3.3.0"},set(progress["pending_versions"]))
        block={b["version"]:b["reason"] for b in progress["blockers"]}
        self.assertEqual("复测失败",block["3.2.0"]); self.assertEqual("尚未登记复测结论",block["3.3.0"])
        item={i["version"]:i for i in progress["versions"]}["3.2.0"]
        self.assertEqual("fail",item["state"]); self.assertEqual("仍可绕过",item["latest"]["note"])
    def test_all_pass_unblocks_resolution(self):
        for v in ["3.1.0","3.2.0","3.3.0"]:
            self.checks.record(self.report,v,self.maint,"复测通过","pass","2026-11-12")
        self.assertTrue(self.checks.progress(self.report)["all_passed"])
        self.db.set_status(self.report,"resolved",self.coord)
        self.assertEqual("resolved",self.db.get_report_for_user(self.report,self.coord)["status"])
    def test_retest_appends_history_and_latest_wins(self):
        self.checks.record(self.report,"3.1.0",self.maint,"首次失败","fail","2026-11-10T09:00:00")
        self.checks.record(self.report,"3.1.0",self.maint,"修复后通过","pass","2026-11-11T09:00:00")
        item={i["version"]:i for i in self.checks.progress(self.report)["versions"]}["3.1.0"]
        self.assertEqual("pass",item["state"])                      # 以最近结果为准
        self.assertEqual(2,item["attempts"])
        self.assertEqual(["fail","pass"],[h["result"] for h in item["history"]])  # 旧结论保留
    def test_fix_plan_change_voids_conclusions(self):
        self.checks.record(self.report,"3.1.0",self.maint,"通过","pass","2026-11-10")
        self.checks.record(self.report,"3.2.0",self.maint,"通过","pass","2026-11-10")
        self.checks.record(self.report,"3.3.0",self.maint,"通过","pass","2026-11-10")
        # 同样内容重复提交：轮次不变，结论保留
        before=self.checks.progress(self.report)
        self.db.set_fix_plan(self.report,self.maint,"三版本统一前置校验", "2026-11-20")
        self.assertEqual(before["revision"],self.checks.progress(self.report)["revision"])
        # 计划内容变化：旧结论全部作废
        self.db.set_fix_plan(self.report,self.maint,"改为网关层规则校验", "2026-11-22")
        progress=self.checks.progress(self.report)
        self.assertEqual(2,progress["revision"])
        self.assertFalse(progress["all_passed"])
        item=progress["versions"][0]
        self.assertEqual("pending",item["state"])
        self.assertEqual("pass",item["voided_latest"]["result"])    # 旧结论仍可查
        self.assertEqual(VOID_REASON,item["blocker"])
        err=self._err(self.db.set_status,self.report,"resolved",self.coord)
        self.assertIn("作废需重新登记",err)
        # 按新内容重登记后才能推进
        for v in ["3.1.0","3.2.0","3.3.0"]:
            self.checks.record(self.report,v,self.maint,"按新计划复测","pass","2026-11-15")
        self.assertTrue(self.checks.progress(self.report)["all_passed"])
        self.db.set_status(self.report,"resolved",self.coord)
    def test_summary_and_version_changes_void_conclusions(self):
        self.checks.record(self.report,"3.1.0",self.maint,"通过","pass","2026-11-10")
        out=self.checks.revise(self.report,self.coord,summary="补充：特制 X-Test 头可绕过")
        self.assertEqual(["summary"],out["changed"])
        self.assertEqual(2,out["revision"])
        item=self.checks.progress(self.report)["versions"][0]
        self.assertEqual("pending",item["state"]); self.assertIsNotNone(item["voided_latest"])
        self.checks.record(self.report,"3.1.0",self.maint,"新摘要下复测通过","pass","2026-11-12")
        # 版本清单变化：新版本待登记，旧版本结论同样作废
        out=self.checks.revise(self.report,self.coord,versions=["3.1.0","3.2.0","3.4.0"])
        self.assertEqual(["versions"],out["changed"])
        progress=self.checks.progress(self.report)
        by={i["version"]:i for i in progress["versions"]}
        self.assertEqual({"3.1.0","3.2.0","3.4.0"},set(by))
        self.assertTrue(all(i["state"]=="pending" for i in progress["versions"]))
        self.assertEqual("pass",by["3.1.0"]["voided_latest"]["result"])
    def test_permissions_and_validation(self):
        # 非成员维护者不能登记
        with self.assertRaisesRegex(DomainError,"维护者"):
            self.checks.record(self.report,"3.1.0",self.outsider,"通过","pass")
        # 协调员不能代登记
        with self.assertRaisesRegex(DomainError,"维护者"):
            self.checks.record(self.report,"3.1.0",self.coord,"通过","pass")
        # 未知版本 / 缺说明 / 错误结果
        with self.assertRaisesRegex(DomainError,"不在受影响版本"):
            self.checks.record(self.report,"9.9.9",self.maint,"通过","pass")
        with self.assertRaisesRegex(DomainError,"处理说明"):
            self.checks.record(self.report,"3.1.0",self.maint,"  ","pass")
        with self.assertRaisesRegex(DomainError,"pass 或 fail"):
            self.checks.record(self.report,"3.1.0",self.maint,"说明","maybe")
        with self.assertRaisesRegex(DomainError,"时间"):
            self.checks.record(self.report,"3.1.0",self.maint,"说明","pass","not-a-date")
        # 非 fixing 状态不能登记（新建报告尚在 new）
        fresh=self.db.create_report("另一漏洞",self.product,self.reporter,"另一个问题","2026-12-15",["3.1.0"],allow_duplicate=True)
        self.db.add_member(fresh,self.maint,"maintainer",self.coord)
        with self.assertRaisesRegex(DomainError,"修复中"):
            self.checks.record(fresh,"3.1.0",self.maint,"通过","pass")
        # 外部维护者不能改摘要；版本不能为空/重复
        with self.assertRaisesRegex(DomainError,"协调员或报告人"):
            self.checks.revise(self.report,self.outsider,summary="x")
        with self.assertRaisesRegex(DomainError,"不能为空"):
            self.checks.revise(self.report,self.coord,versions=["3.1.0"," "])
        with self.assertRaisesRegex(DomainError,"重复"):
            self.checks.revise(self.report,self.coord,versions=["3.1.0","3.1.0"])
        # 全部通过并定稿后，修复计划/摘要/版本冻结；需先退回 fixing
        for v in ["3.1.0","3.2.0","3.3.0"]:
            self.checks.record(self.report,v,self.maint,"复测通过","pass","2026-11-12")
        self.db.set_status(self.report,"resolved",self.coord)
        with self.assertRaisesRegex(DomainError,"已离开修复阶段"):
            self.db.set_fix_plan(self.report,self.maint,"定稿后改计划",None)
        with self.assertRaisesRegex(DomainError,"已离开修复阶段"):
            self.checks.revise(self.report,self.coord,summary="定稿后改摘要")
        self.db.set_status(self.report,"fixing",self.coord)
        self.db.set_fix_plan(self.report,self.maint,"退回后重开修复","2026-11-25")
    def test_notifications_on_failure_and_voiding(self):
        self.checks.record(self.report,"3.2.0",self.maint,"仍可绕过","fail","2026-11-11")
        msgs=[n["message"] for n in self.db.notifications_for(self.coord)]
        self.assertTrue(any("复测失败" in m for m in msgs))
        self.db.set_fix_plan(self.report,self.maint,"全新校验链",None)
        msgs=[n["message"] for n in self.db.notifications_for(self.maint)]
        self.assertTrue(any("作废" in m for m in msgs))

if __name__=="__main__": unittest.main()
