"""v2.95.1 — AI work runs as a background job, so a slow model can no longer
hit the web gateway's 504."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402

H = "neotec_insight.neotec_insight.api.health.health_ai_analysis"
ASK = "neotec_insight.neotec_insight.api.copilot.ask"


class Base(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        frappe.generate_hash = lambda length=20: "job123"
        frappe.local.message_log = []
        from neotec_insight.neotec_insight.utils import ai_jobs
        self.jobs = ai_jobs
        self.calls = []
        test = self

        def fake_target(**kw):
            test.calls.append((test.site.user, kw))
            if kw.get("fail"):
                raise frappe.ValidationError("model said no")
            return {"text": "analysis", "kw": kw}
        self.target = fake_target
        real_get_attr = frappe.get_attr
        frappe.get_attr = lambda path: fake_target if path in (H, ASK) else real_get_attr(path)

    def tearDown(self):
        ff.uninstall()


class TestRunner(Base):
    def test_inline_fallback_without_a_queue(self):
        self.site.user = "acc@a.co"
        r = self.jobs.start(H, {"company": "Alpha"})
        s = self.jobs.status(r["job_id"])
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["result"]["text"], "analysis")
        self.assertEqual(self.calls[0][0], "acc@a.co")             # ran as the user who asked

    def test_queued_then_executed_as_owner(self):
        queued = []
        self.frappe.enqueue = lambda *a, **k: queued.append(k)
        self.site.user = "acc@a.co"
        job = self.jobs.start(ASK, {"question": "q"})["job_id"]
        self.assertEqual(self.jobs.status(job)["status"], "queued")
        self.assertEqual(queued[0]["queue"], "long")
        self.site.user = "Administrator"                            # the worker
        self.jobs.execute(**{k: queued[0][k] for k in ("job_key", "method", "kwargs", "owner")})
        self.assertEqual(self.calls[0][0], "acc@a.co")
        self.assertEqual(self.site.user, "Administrator")           # restored
        self.site.user = "acc@a.co"
        self.assertEqual(self.jobs.status(job)["status"], "done")

    def test_error_is_reported_not_raised(self):
        job = self.jobs.start(H, {"fail": True})["job_id"]
        s = self.jobs.status(job)
        self.assertEqual(s["status"], "error")
        self.assertIn("model said no", s["error"])

    def test_only_the_owner_reads_the_result(self):
        self.site.user = "acc@a.co"
        job = self.jobs.start(H, {})["job_id"]
        self.site.user = "other@a.co"
        with self.assertRaises(self.frappe.PermissionError):
            self.jobs.status(job)

    def test_only_listed_functions_can_be_queued(self):
        with self.assertRaises(self.frappe.ValidationError):
            self.jobs.start("frappe.client.delete", {})

    def test_unknown_job(self):
        self.assertEqual(self.jobs.status("nope")["status"], "missing")


class TestStartEndpoints(Base):
    def test_people_only_user_refused_before_queuing(self):
        self.site.user = "hr@a.co"
        self.site.roles["hr@a.co"] = {"HR Manager"}
        from neotec_insight.neotec_insight.api import copilot, health
        with self.assertRaises(self.frappe.PermissionError):
            health.start_health_ai_analysis()
        with self.assertRaises(self.frappe.PermissionError):
            copilot.start_ask(question="Cash position")
        self.assertEqual(self.calls, [])

    def test_source_access_checked_before_queuing(self):
        from neotec_insight.neotec_insight.api import copilot
        self.site.table("Insight Data Source")["Plant DB"] = {"name": "Plant DB", "enabled": 1, "allowed_roles": "X"}
        self.site.user = "acc@a.co"
        self.site.roles["acc@a.co"] = {"Accounts User"}
        with self.assertRaises(self.frappe.PermissionError):
            copilot.start_ask(question="sales", source="Plant DB")
        self.assertEqual(self.calls, [])


class TestScreensUseJobs(unittest.TestCase):
    SRC = HERE.parents[1] / "reportManager" / "src"

    def test_health_and_copilot_poll_instead_of_waiting(self):
        fh = (self.SRC / "features/health/FinancialHealth.tsx").read_text()
        cp = (self.SRC / "features/copilot/CopilotPanel.tsx").read_text()
        self.assertIn("runAiJob", fh)
        self.assertNotIn("api.healthAiAnalysis(", fh)
        self.assertIn("api.copilotStartAsk(", cp)
        self.assertNotIn("api.copilotAsk(", cp)

    def test_gateway_pages_become_plain_messages(self):
        api = (self.SRC / "utils/api.ts").read_text()
        self.assertIn("504 Gateway Timeout", api)
        self.assertIn("<(style|script)", api)


if __name__ == "__main__":
    unittest.main()
