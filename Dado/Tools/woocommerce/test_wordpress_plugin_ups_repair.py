from contextlib import contextmanager
import argparse, json, tempfile, unittest
from pathlib import Path
from unittest import mock
import wordpress_plugin_deployment_tool as d

class Admin:
    def __init__(self, before, after=None, fail=False):
        self.rows=[before]+([] if after is None else [after]); self.fail=fail; self.uploads=0
    def goto_plugins(self): pass
    def read_row(self): return self.rows.pop(0)
    def upload_ups_repair(self, path):
        self.uploads += 1
        if self.fail: raise RuntimeError("blocked")
        if Path(path).resolve()!=Path(d.UPS_REPAIR_ARTIFACT_PATH).resolve(): raise AssertionError(path)
        return {"comparison_name":d.PLUGIN_NAME,"comparison_uploaded_version":d.UPS_REPAIR_VERSION}

class UpsRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.old=(d.PLAN_DIR,d.RECEIPTS)
        d.PLAN_DIR=self.root/"plans"; d.RECEIPTS=self.root/"receipts.jsonl"
        self.before=d.project_row(True,True,d.UPS_REPAIR_FROM_VERSION,False)
        self.after=d.project_row(True,True,d.UPS_REPAIR_VERSION,False)
    def tearDown(self):
        d.PLAN_DIR,d.RECEIPTS=self.old; self.tmp.cleanup()
    def artifact(self): return d.verify_ups_repair_artifact()
    def plan(self):
        return d.stage_plan("plugin_ups_repair",self.before,self.after,self.artifact(),None)
    def args(self,path,approval="APPROVED"):
        return argparse.Namespace(plan=str(path),approval=approval)
    @contextmanager
    def session(self,admin): yield admin
    def rehash(self,path,mutate):
        p=json.loads(path.read_text(encoding="utf-8")); p.pop("sha256")
        mutate(p); p["sha256"]=d.digest_for(p)
        path.write_text(json.dumps(p,indent=2)+"\n",encoding="utf-8")

    def test_fixed_artifact_and_disclosures(self):
        a=self.artifact()
        self.assertEqual(a["sha256"],d.UPS_REPAIR_SHA256)
        self.assertEqual(a["version"],"2.0.5")
        c=d.UPS_REPAIR_VALIDATION_CONTRACT
        self.assertEqual(c["allowlisted_variations"],64)
        self.assertEqual(c["physically_verified_groups"],0)
        self.assertFalse(c["automatic_rollback"])
        self.assertFalse(c["creates_shipping_rate"])

    def test_wrong_artifact_path_refused(self):
        with self.assertRaises(d.DeploymentError):
            d.verify_ups_repair_artifact(Path(__file__))

    def test_closed_plan_and_tamper_refusal(self):
        path=self.plan(); plan=d.load_plan(str(path))
        self.assertEqual(plan["action"],"plugin_ups_repair")
        self.assertEqual(plan["after_expected"],self.after)
        self.rehash(path,lambda p:p["validation"].update({"physically_verified_groups":30}))
        with self.assertRaises(d.DeploymentError): d.load_plan(str(path))

    def test_stage_requires_exact_active_204(self):
        with mock.patch.object(d,"_live_row",return_value=self.before), \
             mock.patch.object(d,"_stage_and_report") as staged:
            d.command_stage_ups_repair(argparse.Namespace())
        staged.assert_called_once()
        bad=d.project_row(True,False,d.UPS_REPAIR_FROM_VERSION,False)
        with mock.patch.object(d,"_live_row",return_value=bad):
            with self.assertRaises(d.DeploymentError):
                d.command_stage_ups_repair(argparse.Namespace())

    def test_commit_happy_path_locks_and_defers_public_checks(self):
        path=self.plan(); admin=Admin(self.before,self.after)
        with mock.patch.object(d,"admin_session",lambda:self.session(admin)):
            d.command_commit_ups_repair.__wrapped__(self.args(path))
        lock=json.loads(d.lock_path(path).read_text())
        result=json.loads(d.result_path(path).read_text())
        self.assertEqual(admin.uploads,1)
        self.assertEqual(lock["status"],"plugin_row_verified")
        self.assertTrue(lock["public_validation_pending"])
        self.assertEqual(result["status"],"PLUGIN_ROW_VERIFIED_PUBLIC_CHECKS_PENDING")
        with mock.patch.object(d,"admin_session",side_effect=AssertionError("replay")):
            with self.assertRaises(d.DeploymentError):
                d.command_commit_ups_repair.__wrapped__(self.args(path))

    def test_approval_and_drift_are_free_refusals(self):
        path=self.plan()
        with mock.patch.object(d,"admin_session",side_effect=AssertionError("network")):
            with self.assertRaises(d.DeploymentError):
                d.command_commit_ups_repair.__wrapped__(self.args(path,"approved"))
        self.assertFalse(d.lock_path(path).exists())
        drift=d.project_row(True,True,"2.0.2",False); admin=Admin(drift)
        with mock.patch.object(d,"admin_session",lambda:self.session(admin)):
            with self.assertRaises(d.DeploymentError):
                d.command_commit_ups_repair.__wrapped__(self.args(path))
        self.assertFalse(d.lock_path(path).exists()); self.assertEqual(admin.uploads,0)

    def test_upload_failure_is_indeterminate_and_no_retry(self):
        path=self.plan(); admin=Admin(self.before,fail=True)
        with mock.patch.object(d,"admin_session",lambda:self.session(admin)):
            with self.assertRaises(d.IndeterminateError):
                d.command_commit_ups_repair.__wrapped__(self.args(path))
        lock=json.loads(d.lock_path(path).read_text()); result=json.loads(d.result_path(path).read_text())
        self.assertEqual(lock["status"],"indeterminate"); self.assertFalse(result["retry"])

    def test_parser_exposes_only_fixed_commands(self):
        p=d.build_parser()
        for cmd in ("stage-ups-repair","commit-ups-repair"):
            ns=p.parse_args([cmd]+([] if cmd.startswith("stage") else ["--plan","x","--approval","APPROVED"]))
            self.assertTrue(callable(ns.func))

if __name__=="__main__": unittest.main()
