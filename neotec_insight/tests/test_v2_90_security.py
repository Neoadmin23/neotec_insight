"""v2.90.0 — fixes for the 2.89.6 health check (HC-01 … HC-13).

Each class names the finding it pins. Run against the in-memory site
(tests/_fakefrappe.py), which models users, roles, permission rules, User
Permissions on Company, savepoints and a per-site shared cache."""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # package dir
APP = ROOT / "neotec_insight"
REPO = ROOT.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
import _fakefrappe as ff  # noqa: E402


class Site(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.metas.setdefault("Company", {"name": "Company", "fields": []})
        for c in ("Alpha Co", "Beta Co"):
            self.site.table("Company")[c] = {"name": c, "company_name": c, "year_start_date": "2026-01-01"}
        frappe.db.commit()

    def tearDown(self):
        ff.uninstall()

    def as_user(self, user, roles, companies=None):
        self.site.user = user
        self.site.roles[user] = set(roles)
        if companies is not None:
            self.site.allowed_companies[user] = list(companies)
        self.frappe.local = types.SimpleNamespace(site=self.site.site_name)  # fresh request

    def request(self, method, **form):
        self.frappe.request = types.SimpleNamespace(path=f"/api/method/{method}")
        self.frappe.local = types.SimpleNamespace(site=self.site.site_name, form_dict=dict(form))
        from neotec_insight.neotec_insight.utils.access import enforce_request_scope
        enforce_request_scope()
        return self.frappe.local.form_dict


# ── HC-01 company / report scope ─────────────────────────────────────────────

class TestHC01CompanyAndReportScope(Site):
    def test_named_company_outside_scope_is_refused(self):
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co"])
        self.request("neotec_insight.neotec_insight.api.ageing.get_ageing", company="Alpha Co")
        with self.assertRaises(self.frappe.PermissionError):
            self.request("neotec_insight.neotec_insight.api.ageing.get_ageing", company="Beta Co")

    def test_companies_list_argument_is_checked(self):
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co"])
        with self.assertRaises(self.frappe.PermissionError):
            self.request("neotec_insight.neotec_insight.api.report.x", companies='["Alpha Co","Beta Co"]')

    def test_restricted_user_naming_no_company_gets_their_one_company(self):
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co"])
        form = self.request("neotec_insight.neotec_insight.api.cfo.morning_brief")
        self.assertEqual(form["company"], "Alpha Co")

    def test_restricted_to_several_companies_must_choose(self):
        self.site.table("Company")["Gamma Co"] = {"name": "Gamma Co", "company_name": "Gamma Co"}
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co", "Beta Co"])
        with self.assertRaises(self.frappe.PermissionError):
            self.request("neotec_insight.neotec_insight.api.cfo.morning_brief")

    def test_unrestricted_user_is_not_changed(self):
        self.as_user("cfo@a.co", {"Accounts Manager"}, ["Alpha Co", "Beta Co"])
        form = self.request("neotec_insight.neotec_insight.api.cfo.morning_brief")
        self.assertNotIn("company", form)

    def test_non_insight_requests_are_untouched(self):
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co"])
        self.request("frappe.client.get_list", company="Beta Co")      # not ours

    def test_list_companies_respects_user_permissions(self):
        from neotec_insight.neotec_insight.api.report import list_companies
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co"])
        self.assertEqual([c["name"] for c in list_companies()], ["Alpha Co"])

    def test_inactive_report_is_refused_and_not_listed_for_non_managers(self):
        f = self.frappe
        f.get_doc({"doctype": "Insight Report Definition", "slug": "old-pl", "report_name": "Old",
                   "report_type": "pnl", "version": 1, "definition_json": "{}", "is_active": 0,
                   "owner": "someone@a.co"}).insert(set_name="old-pl")
        f.db.commit()

        def perm(doctype, ptype, doc, user):
            if doctype == "Insight Report Definition" and doc is not None:
                d = doc if hasattr(doc, "is_active") else None
                return bool(d and d.is_active)
            return None
        self.site.perm = perm
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co", "Beta Co"])
        from neotec_insight.neotec_insight.api.report import _resolve_report_doc, list_reports
        with self.assertRaises(f.PermissionError):
            _resolve_report_doc("old-pl")
        self.assertNotIn("old-pl", [r["name"] for r in list_reports(include_disabled=1)])
        with self.assertRaises(f.PermissionError):
            self.request("neotec_insight.neotec_insight.api.report.get_report", report="old-pl")

    def test_report_of_a_company_outside_scope_is_refused(self):
        f = self.frappe
        f.get_doc({"doctype": "Insight Report Definition", "slug": "beta-pl", "report_name": "Beta",
                   "report_type": "pnl", "version": 1, "definition_json": "{}", "is_active": 1,
                   "company": "Beta Co"}).insert(set_name="beta-pl")
        f.db.commit()
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co"])
        with self.assertRaises(f.PermissionError):
            self.request("neotec_insight.neotec_insight.api.report.run_report", report="beta-pl")

    def test_dimension_record_without_permission_is_refused(self):
        self.site.table("Cost Center")["Secret - B"] = {"name": "Secret - B"}
        self.site.perm = lambda dt, p, doc, u: (doc != "Secret - B") if dt == "Cost Center" else None
        self.as_user("acc@a.co", {"Accounts User"}, ["Alpha Co", "Beta Co"])
        with self.assertRaises(self.frappe.PermissionError):
            self.request("neotec_insight.neotec_insight.api.report.run_report", cost_center="Secret - B")


# ── HC-02 People / payroll ───────────────────────────────────────────────────

class TestHC02People(Site):
    def setUp(self):
        super().setUp()
        self.site.metas["Salary Slip"] = {"name": "Salary Slip", "fields": []}
        self.site.perm = lambda dt, p, doc, u: ("HR Manager" in self.site.roles.get(u, set())) \
            if dt in ("Salary Slip", "Employee") else (
                "Accounts User" in self.site.roles.get(u, set()) or "Accounts Manager" in self.site.roles.get(u, set())
            ) if dt == "GL Entry" else None
        from neotec_insight.neotec_insight.utils import access
        self.access = access

    def test_ledger_reader_without_payroll_rights_cannot_read_wages(self):
        self.as_user("acc@a.co", {"Accounts User"})
        with self.assertRaises(self.frappe.PermissionError):
            self.access.require_people_read()

    def test_hr_only_user_reads_people_but_not_finance(self):
        self.as_user("hr@a.co", {"HR Manager"})
        self.access.require_people_read()
        with self.assertRaises(self.frappe.PermissionError):
            self.access.require_finance_read()

    def test_executive_reads_both(self):
        self.as_user("cfo@a.co", {"Insight CFO", "Accounts Manager"})
        self.access.require_people_read()
        self.access.require_finance_read()

    def test_every_people_endpoint_uses_the_people_gate(self):
        src = (APP / "api" / "cfo.py").read_text()
        tree = ast.parse(src)
        for name in ("hr_summary", "eosb_breakdown", "provision_field_options",
                     "get_provision_config", "set_provision_config"):
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            body = ast.unparse(fn)
            self.assertIn("_require_people()", body, name)
            self.assertNotIn("_require_read()", body, name)

    def test_every_module_gate_delegates_to_the_one_policy(self):
        for f in (APP / "api").glob("*.py"):
            tree = ast.parse(f.read_text())
            fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_require_read"), None)
            if fn:
                self.assertIn("require_finance_read", ast.unparse(fn), f.name)


# ── HC-03 private files ──────────────────────────────────────────────────────

class TestHC03Files(Site):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.frappe.get_site_path = lambda *p: os.path.join(self.tmp, *p)
        os.makedirs(os.path.join(self.tmp, "private", "files"))
        self.path = os.path.join(self.tmp, "private", "files", "stmt.csv")
        open(self.path, "w").write("a,b\n")
        self.site.metas["File"] = {"name": "File", "fields": []}
        self.site.table("File")["F1"] = {"name": "F1", "file_url": "/private/files/stmt.csv",
                                          "file_name": "stmt.csv", "owner": "owner@a.co"}
        orig = ff.Document.__getattr__
        test = self

        def ga(doc, k):
            if k == "get_full_path":
                return lambda: test.path
            return orig(doc, k)
        ff.Document.__getattr__ = ga
        self.addCleanup(lambda: setattr(ff.Document, "__getattr__", orig))
        from neotec_insight.neotec_insight.utils import access
        self.access = access

    def test_other_users_private_file_is_refused(self):
        self.site.perm = lambda dt, p, doc, u: (getattr(doc, "owner", None) == u) if dt == "File" else None
        self.as_user("intruder@a.co", {"Accounts User"})
        with self.assertRaises(self.frappe.PermissionError):
            self.access.readable_file("/private/files/stmt.csv", self.access.STATEMENT_EXTS)
        self.as_user("owner@a.co", {"Accounts User"})
        _doc, path = self.access.readable_file("/private/files/stmt.csv", self.access.STATEMENT_EXTS)
        self.assertEqual(path, os.path.realpath(self.path))

    def test_type_size_and_location_are_bounded(self):
        self.as_user("owner@a.co", {"Accounts User"})
        with self.assertRaises(self.frappe.ValidationError):
            self.access.readable_file("/private/files/stmt.csv", {".pdf"})
        with self.assertRaises(self.frappe.ValidationError):
            self.access.readable_file("/private/files/stmt.csv", None, max_bytes=1)
        self.path = "/etc/hosts"
        with self.assertRaises(self.frappe.PermissionError):
            self.access.readable_file("/private/files/stmt.csv")

    def test_both_readers_go_through_it(self):
        self.assertIn("readable_file", (APP / "api" / "statement_reader.py").read_text())
        src = (APP / "api" / "bank_reader.py").read_text()
        self.assertIn("readable_file(file_url, SLIP_EXTS)", src)
        self.assertNotIn('frappe.get_doc("File", {"file_url": file_url})', src)


# ── HC-04 Report Studio fields and joins ─────────────────────────────────────

class TestHC04Studio(Site):
    def setUp(self):
        super().setUp()
        self.site.metas["Customer"] = {"name": "Customer", "fields": [
            {"fieldname": "customer_name", "fieldtype": "Data", "permlevel": 0},
            {"fieldname": "credit_limit", "fieldtype": "Currency", "permlevel": 1},
            {"fieldname": "api_secret", "fieldtype": "Password", "permlevel": 0}]}
        meta_cls = ff._Meta

        def permitted(self_meta, *a, **k):
            return [f["fieldname"] for f in self_meta._d["fields"] if not f.get("permlevel")]
        meta_cls.get_permitted_fieldnames = permitted
        self.addCleanup(lambda: delattr(meta_cls, "get_permitted_fieldnames"))
        from neotec_insight.neotec_insight.api import studio
        self.studio = studio

    def test_password_and_higher_permlevel_fields_are_not_readable(self):
        self.as_user("acc@a.co", {"Accounts User"})
        r = self.studio._readable_fieldnames("Customer")
        self.assertIn("customer_name", r)
        self.assertNotIn("credit_limit", r)
        self.assertNotIn("api_secret", r)

    def test_joined_lookups_are_permission_aware(self):
        src = (APP / "api" / "studio.py").read_text()
        run = src[src.index("def run_query("):src.index("def ai_build(")]
        self.assertIn("frappe.get_list(target", run)
        self.assertNotIn("frappe.get_all(target", run)
        self.assertIn("tf in _readable_fieldnames(link_opts[lf])", run)


# ── HC-05 formulas ───────────────────────────────────────────────────────────

class TestHC05Formulas(Site):
    def setUp(self):
        super().setUp()
        from neotec_insight.neotec_insight.utils import formula
        self.f = formula

    def test_if_evaluates_only_the_chosen_branch(self):
        self.assertEqual(self.f.evaluate_row_formula("IF(0, 1/0, 42)", {}), 42.0)
        self.assertEqual(self.f.evaluate_row_formula("IF(1, 42, 1/0)", {}), 42.0)
        self.assertEqual(self.f.evaluate_row_formula("IF(a <> 0, b / a, 0)".replace("<>", "!="),
                                                     {"a": 0, "b": 5}), 0.0)

    def test_division_by_zero_is_an_error_not_a_zero(self):
        with self.assertRaises(self.f.FormulaError):
            self.f.evaluate_row_formula("a / b", {"a": 1, "b": 0})

    def test_arity_and_keywords_are_validated(self):
        for bad in ("IF(1, 2)", "ROUND(1, 2, 3)", "ABS()", "MIN()", "ROUND(x=1)"):
            with self.assertRaises(self.frappe.ValidationError, msg=bad):
                self.f.validate_formula_expression(bad, {"x"}, row_key="r")
        self.f.validate_formula_expression("IF(x > 0, ROUND(x, 2), SUM())", {"x"}, row_key="r")

    def test_engine_reports_failed_cells(self):
        src = (APP / "utils" / "execution.py").read_text()
        self.assertIn('"formula_errors": formula_errors', src)
        self.assertIn('formula_errors[key]["months"][str(m)]', src)
        self.assertIn('"formula_errors": current.get("formula_errors")', (APP / "api" / "report.py").read_text())


# ── HC-06 caches ─────────────────────────────────────────────────────────────

class TestHC06Caches(Site):
    def fresh_request(self, site):
        self.site.site_name = site
        self.frappe.local = types.SimpleNamespace(site=site)

    def test_fiscal_calendar_is_per_site(self):
        from neotec_insight.neotec_insight.utils.fiscal_year import get_company_fy_start_month
        self.site.table("Company")["Same Co"] = {"name": "Same Co", "year_start_date": "2026-01-01"}
        self.fresh_request("site-a")
        self.assertEqual(get_company_fy_start_month("Same Co"), 1)
        self.site.table("Company")["Same Co"]["year_start_date"] = "2026-04-01"   # site B's data
        self.fresh_request("site-b")
        self.assertEqual(get_company_fy_start_month("Same Co"), 4)
        self.fresh_request("site-a")
        self.assertEqual(get_company_fy_start_month("Same Co"), 1)               # A's own cache

    def test_company_edit_clears_the_shared_cache(self):
        from neotec_insight.neotec_insight.utils import fiscal_year as fy
        self.site.table("Company")["Same Co"] = {"name": "Same Co", "year_start_date": "2026-01-01"}
        self.fresh_request("site-a")
        self.assertEqual(fy.get_company_fy_start_month("Same Co"), 1)
        self.site.table("Company")["Same Co"]["year_start_date"] = "2026-07-01"
        fy.clear_company_fy_cache_on_company_save(types.SimpleNamespace(name="Same Co"))
        self.fresh_request("site-a")                                              # another worker
        self.assertEqual(fy.get_company_fy_start_month("Same Co"), 7)

    def test_new_account_appears_after_invalidation(self):
        from neotec_insight.neotec_insight.utils import execution as ex
        self.site.table("Account")["4100"] = {"name": "4100", "root_type": "Income", "is_group": 0,
                                               "company": "Alpha Co"}
        self.fresh_request("site-a")
        self.assertEqual(ex._income_expense_accounts("Alpha Co"), ["4100"])
        self.site.table("Account")["4200"] = {"name": "4200", "root_type": "Income", "is_group": 0,
                                               "company": "Alpha Co"}
        ex.invalidate_account_cache(types.SimpleNamespace(company="Alpha Co"))
        self.fresh_request("site-a")
        self.assertEqual(sorted(ex._income_expense_accounts("Alpha Co")), ["4100", "4200"])

    def test_account_hooks_registered(self):
        h = (ROOT / "hooks.py").read_text()
        for ev in ("after_insert", "on_update", "on_trash", "after_rename"):
            self.assertIn(f'"{ev}": "neotec_insight.neotec_insight.utils.execution.invalidate_account_cache"', h)


# ── HC-07 CFO windows ────────────────────────────────────────────────────────

class TestHC07Cfo(Site):
    def setUp(self):
        super().setUp()
        from neotec_insight.neotec_insight.api import cfo
        self.cfo = cfo

    def test_same_elapsed_days(self):
        self.assertEqual([str(d) for d in self.cfo._same_elapsed("2026-10-15", 1)], ["2026-09-01", "2026-09-15"])
        self.assertEqual([str(d) for d in self.cfo._same_elapsed("2026-03-31", 1)], ["2026-02-01", "2026-02-28"])
        self.assertEqual([str(d) for d in self.cfo._same_elapsed("2026-10-15", 12)], ["2025-10-01", "2025-10-15"])

    def test_runway_uses_three_full_months_of_cash(self):
        calls = []
        self.cfo._accounts = lambda company, **f: ["acc"]

        def mov(company, accts, d1, d2, signed="dr"):
            calls.append((str(d1), str(d2)))
            return -300.0 if len(calls) == 1 else 900.0
        self.cfo._gl_movement = mov
        r = self.cfo._runway("Alpha Co", 1000.0, "2026-10-15")
        self.assertEqual(calls[0], ("2026-07-01", "2026-09-30"))
        self.assertEqual((r["monthly_burn"], r["runway_months"]), (100.0, 10.0))
        self.assertEqual(r["expense_run_rate"], 300.0)
        self.assertEqual(r["basis"], "net_cash_outflow_last_3_full_months")

    def test_receivables_are_as_of_and_uncapped(self):
        src = (APP / "api" / "cfo.py").read_text()
        body = src[src.index("def _ple_outstanding"):src.index("def _receivables")]
        self.assertIn("posting_date <= %(as_of)s", body)
        self.assertNotIn("limit_page_length=5000", src[src.index("def _receivables"):src.index("def _vat")])


# ── HC-08 type-check gate ────────────────────────────────────────────────────

class TestHC08Gate(unittest.TestCase):
    def run_gate(self, tsc_body):
        d = tempfile.mkdtemp()
        os.makedirs(f"{d}/scripts")
        os.makedirs(f"{d}/node_modules/.bin")
        Path(f"{d}/scripts/typecheck.sh").write_text((REPO / "reportManager/scripts/typecheck.sh").read_text())
        tsc = Path(f"{d}/node_modules/.bin/tsc")
        tsc.write_text("#!/bin/sh\n" + tsc_body)
        tsc.chmod(0o755)
        return subprocess.run(["bash", f"{d}/scripts/typecheck.sh"], capture_output=True, text=True).returncode

    def test_fatal_compiler_error_fails(self):
        self.assertEqual(self.run_gate('echo "error TS5083: Cannot read file tsconfig.json."\nexit 1\n'), 1)

    def test_silent_crash_fails(self):
        self.assertEqual(self.run_gate("exit 2\n"), 1)

    def test_error_with_zero_exit_fails(self):
        self.assertEqual(self.run_gate('echo "src/a.ts(1,1): error TS2322: x"\nexit 0\n'), 1)

    def test_clean_compile_passes(self):
        self.assertEqual(self.run_gate("exit 0\n"), 0)

    def test_no_baseline_and_lint_configured(self):
        rm = REPO / "reportManager"
        self.assertFalse((rm / "scripts/typecheck-baseline.txt").exists())
        self.assertTrue((rm / "eslint.config.js").exists())
        pkg = json.loads((rm / "package.json").read_text())
        self.assertIn("eslint", pkg["devDependencies"])
        self.assertIn("eslint", pkg["scripts"]["build"])


# ── HC-09 dependencies ───────────────────────────────────────────────────────

class TestHC09Dependencies(unittest.TestCase):
    def test_locked_versions(self):
        pkg = json.loads((REPO / "reportManager/package.json").read_text())
        deps = {**pkg["dependencies"], **pkg["devDependencies"]}
        self.assertNotIn("frappe-react-sdk", deps)
        self.assertEqual(deps["jspdf"], "4.2.1")
        self.assertEqual(deps["jspdf-autotable"], "5.0.8")
        self.assertEqual(deps["vite"], "6.4.3")
        self.assertEqual(pkg["overrides"]["postcss"], "8.5.28")
        lock = json.loads((REPO / "reportManager/package-lock.json").read_text())
        self.assertFalse(any("socket.io" in k or k.endswith("/axios") for k in lock["packages"]))


# ── HC-10 PDF resources ──────────────────────────────────────────────────────

class TestHC10Pdf(Site):
    def test_only_own_and_inline_resources_survive(self):
        from neotec_insight.neotec_insight.api import pdf
        hosts = {"neo15.k.frappe.cloud"}
        h = ('<style>@import url("https://evil.example/x.css");.a{background:url(http://127.0.0.1/x)}'
             '.b{background:url(data:image/png;base64,AA)}</style>'
             '<img src="http://169.254.169.254/latest"><img src="https://neo15.k.frappe.cloud/files/l.png">'
             '<img src="/files/l.png"><img src=file:///etc/passwd><img src="gopher://x">'
             '<img srcset="https://a.com/1.png 1x, /files/2.png 2x"><a href="https://evil.example">x</a>')
        out = pdf._restrict_resources(h, hosts)
        for bad in ("evil.example", "127.0.0.1", "169.254", "file:", "gopher:", "a.com", "@import"):
            self.assertNotIn(bad, out, bad)
        for good in ("https://neo15.k.frappe.cloud/files/l.png", 'src="/files/l.png"', "data:image/png"):
            self.assertIn(good, out, good)


# ── HC-11 methods ────────────────────────────────────────────────────────────

READ_WITH_CACHE_WRITE = {
    "allocation.get_grid", "allocation.run", "allocation.diagnose", "report.get_csrf",
    "analytics.get_fragility_radar", "cash_flow_forecast.download_sample_history_template",  # moved from report in v2.92.0
    "cash_flow_forecast.download_sample_statement_template", "budget_import.budget_import_template",
    "studio.export_xlsx", "vat.vat_return", "packs.generate_pack"}
MUT = {"insert", "save", "submit", "cancel", "delete_doc", "db_set", "commit", "sendmail", "enqueue",
       "rename_doc", "db_insert", "db_update", "new_doc"}


class TestHC11Methods(unittest.TestCase):
    def endpoints(self):
        for f in sorted((APP / "api").glob("*.py")):
            t = ast.parse(f.read_text())
            defs = {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)}
            for n in defs.values():
                dec = next((d for d in n.decorator_list if "whitelist" in ast.unparse(d)), None)
                if dec is None:
                    continue
                seen, calls, todo = {n.name}, set(), [n]
                while todo:
                    for c in ast.walk(todo.pop()):
                        if isinstance(c, ast.Call):
                            nm = c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None)
                            calls.add(nm)
                            if nm in defs and nm not in seen:
                                seen.add(nm)
                                todo.append(defs[nm])
                yield f"{f.stem}.{n.name}", "methods" in ast.unparse(dec), ast.unparse(dec), calls

    def test_every_mutating_endpoint_is_post_only(self):
        open_mut = [k for k, restricted, _d, calls in self.endpoints()
                    if not restricted and calls & MUT and k not in READ_WITH_CACHE_WRITE
                    and not k.startswith("report.run_")]
        self.assertEqual(open_mut, [])

    def test_frontend_uses_post_for_post_only_endpoints(self):
        post_only = {k for k, _r, d, _c in self.endpoints() if '"POST"' in d and "GET" not in d}
        src = "".join(p.read_text() for p in (REPO / "reportManager/src").rglob("*.ts*"))
        bad = []
        for m in re.finditer(r"call(?:<[^()]*?>)?\(\s*'([a-z_]+\.[a-z_0-9]+)'", src):
            if m.group(1) not in post_only:
                continue
            i, depth = m.end(), 1
            while depth and i < len(src):
                depth += {"(": 1, ")": -1}.get(src[i], 0)
                i += 1
            if "'POST'" not in src[m.start():i]:
                bad.append(m.group(1))
        self.assertEqual(sorted(set(bad)), [])


# ── HC-12 statement import outcomes ──────────────────────────────────────────

class TestHC12StatementImport(Site):
    def setUp(self):
        super().setUp()
        self.site.table("Bank Account")["BA1"] = {"name": "BA1", "company": "Alpha Co"}
        from neotec_insight.neotec_insight.api import statement_reader as sr
        self.sr = sr
        sr._file_path = lambda url: "/tmp/x.csv"
        self.lines = [{"value_date": "2026-10-01", "credit": 100, "debit": 0, "reference": "R1",
                       "txn_type": "T", "details": "d"},
                      {"value_date": "2026-10-02", "credit": 50, "debit": 0, "reference": "R2",
                       "txn_type": "T", "details": "d"},
                      {"value_date": "", "credit": 0, "debit": 0, "reference": "R3",
                       "txn_type": "T", "details": "d"}]
        sr._read_statement_any = lambda path: ("account", self.lines, None)
        self.fail_refs = {"R2"}
        test = self

        def submit(doc):
            if doc.reference_number in test.fail_refs:
                raise ff.ValidationError("Bank account has no GL account")
            doc.docstatus = 1
            doc.save()
        ff.Document.submit = submit
        self.addCleanup(lambda: delattr(ff.Document, "submit"))
        self.site.metas["Bank Transaction"] = {"name": "Bank Transaction", "fields": [
            {"fieldname": "docstatus", "fieldtype": "Int", "default": "0"}]}

    def test_each_line_ends_in_one_honest_state(self):
        r = self.sr.import_statement("/private/files/x.csv", "BA1")
        self.assertEqual(r["created_count"], 1)
        self.assertEqual([d["reference"] for d in r["drafts"]], ["R2"])
        self.assertIn("no GL account", r["drafts"][0]["error"])
        self.assertEqual(len(r["failed"]), 1)

    def test_reimport_retries_drafts_instead_of_skipping_them(self):
        self.sr.import_statement("/private/files/x.csv", "BA1")
        self.fail_refs = set()
        r = self.sr.import_statement("/private/files/x.csv", "BA1")
        self.assertEqual(len(r["retried"]), 1)
        self.assertEqual(r["skipped"], 1)            # R1 was already submitted
        self.assertEqual(r["drafts"], [])


# ── HC-13 snapshots, restore, schedules ──────────────────────────────────────

class TestHC13Guard(Site):
    def setUp(self):
        super().setUp()
        from neotec_insight.neotec_insight.utils import snapshots
        self.snap = snapshots

    def test_failed_snapshot_neither_commits_nor_undoes_the_callers_work(self):
        f = self.frappe
        self.site.table("Insight Dashboard")["D1"] = {"name": "D1", "title": "before"}   # uncommitted write
        real = self.snap.take
        self.snap.take = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
        try:
            self.assertIsNone(self.snap.guard("pre_change", sections=["Insight Dashboard"]))
        finally:
            self.snap.take = real
        self.assertTrue(f.db.exists("Insight Dashboard", "D1"))          # caller's write survived
        f.db.rollback()
        self.assertFalse(f.db.exists("Insight Dashboard", "D1"))         # and was never committed

    def test_required_snapshot_stops_the_operation(self):
        real = self.snap.take
        self.snap.take = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
        try:
            with self.assertRaises(self.snap.SnapshotRequired):
                self.snap.guard("pre_restore", required=True)
        finally:
            self.snap.take = real

    def test_incomplete_snapshot_is_marked_and_blocks_required_operations(self):
        from neotec_insight.neotec_insight.utils import backup_engine
        real = backup_engine.build_bundle

        def partial(sections=None, plan=None):
            b = real(sections, plan)
            b["errors"] = ["Insight Budget Cell: timeout"]
            return b
        self.snap.build_bundle = partial
        r = self.snap.take("manual")
        self.assertTrue(r["incomplete"])
        self.assertIn("INCOMPLETE", self.frappe.db.get_value(self.snap.DT, r["name"], "alert"))
        with self.assertRaises(self.snap.SnapshotRequired):
            self.snap.guard("pre_restore", required=True, sections=["Insight Dashboard"])

    def test_destructive_callers_require_the_snapshot(self):
        for f, n in (("api/allocation.py", 5), ("api/recovery.py", 3), ("utils/map_importer.py", 1)):
            self.assertGreaterEqual((APP / f).read_text().count("required=True"), n, f)
        for f in ("api/config_backup.py", "api/backup.py"):   # import_configuration moved in v2.91.0
            self.assertIn("required=not cint(allow_without_snapshot)", (APP / f).read_text())


class TestHC13Restore(Site):
    def test_orphans_and_version_are_reported(self):
        f = self.frappe
        from neotec_insight.neotec_insight.utils import backup_engine as be
        bundle = {"version": "1.0.0", "data": {"Insight Allocation Entry": [
            {"name": "E1", "rule": "Ghost Rule", "cost_center": "CC", "period_month": "2026-01-01",
             "basis": "head_count", "driver_value": 1}]}}
        s = be.restore_bundle(bundle, mode="merge")
        self.assertFalse(s["rolled_back"], s["errors"])
        self.assertEqual(s["orphans"], {"Insight Allocation Entry.rule": 1})
        self.assertIn("v1.0.0", s["version_note"])
        f.db.rollback()


class TestHC13Schedules(Site):
    def setUp(self):
        super().setUp()
        self.site.table("User")["acc@a.co"] = {"name": "acc@a.co", "email": "acc@a.co", "enabled": 1}
        self.site.table("Studio Report")["R1"] = {"name": "R1", "title": "R", "config_json": '{"doctype": "GL Entry"}'}
        from neotec_insight.neotec_insight.utils import scheduled_reports as sch
        self.sch = sch
        self.seen = []
        studio = types.ModuleType("neotec_insight.neotec_insight.api.studio")
        studio.run_query = lambda cfg: self.seen.append(self.site.user) or {"rows": [], "columns": []}
        sys.modules["neotec_insight.neotec_insight.api.studio"] = studio
        sch._render_attachment = lambda *a: ("r.xlsx", None)

    def test_runs_as_the_person_who_saved_it(self):
        doc = ff.Document("Insight Report Schedule", {"name": "S1", "report": "R1", "run_as": "acc@a.co",
                                                     "owner": "Administrator", "recipients": ""})
        doc.db_set = lambda *a, **k: None
        self.sch.dispatch_schedule(doc)
        self.assertEqual(self.seen, ["acc@a.co"])
        self.assertEqual(self.site.user, "Administrator")            # restored afterwards

    def test_disabled_owner_fails_instead_of_running(self):
        self.site.table("User")["acc@a.co"]["enabled"] = 0
        doc = ff.Document("Insight Report Schedule", {"name": "S1", "report": "R1", "run_as": "acc@a.co"})
        doc.db_set = lambda *a, **k: None
        self.sch.dispatch_schedule(doc)
        self.assertEqual(self.seen, [])
        self.assertTrue(any("dispatch failed" in e for e in self.site.errors))

    def test_recipient_policy(self):
        self.as_user("acc@a.co", {"Accounts Manager"})
        self.sch.check_recipients("acc@a.co", "")
        with self.assertRaises(self.frappe.ValidationError):
            self.sch.check_recipients("someone@gmail.com", "")
        with self.assertRaises(self.frappe.ValidationError):
            self.sch.check_recipients("", "+966500000000")
        self.frappe.conf["insight_schedule_allowed_domains"] = ["irsaa.sa"]
        self.sch.check_recipients("cfo@irsaa.sa", "")
        with self.assertRaises(self.frappe.ValidationError):
            self.sch.check_recipients("acc@a.co", "")


class TestReleaseHygiene(unittest.TestCase):
    def test_erpnext_declared_and_patch_present(self):
        h = (ROOT / "hooks.py").read_text()
        self.assertIn('required_apps = ["erpnext"]', h)
        self.assertIn("utils.access.enforce_request_scope", h)
        self.assertTrue((ROOT / "patches/v2_90/security_fields.py").exists())


if __name__ == "__main__":
    unittest.main()
