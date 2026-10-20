"""v2.95.0 — Copilot on any ERPNext document type and on outside databases.

Outside-database queries run for real against SQLite standing in for the
source, so the guard, the row cap and the type handling are exercised by an
actual SQL engine."""
import datetime
import decimal
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "neotec_insight" / "utils"))
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import copilot_core as core  # noqa: E402
import sql_guard as g  # noqa: E402
import _fakefrappe as ff  # noqa: E402

ALLOW = {"sales", "items"}


class TestGuard(unittest.TestCase):
    OK = [
        "SELECT customer, SUM(amount) AS total FROM sales GROUP BY customer ORDER BY total DESC",
        "WITH m AS (SELECT * FROM sales) SELECT open, close, REPLACE(customer,'a','b') FROM m JOIN items i ON i.id = m.item",
        "select region, count(*) from sales where note = 'drop table x; -- not a comment' group by region",
        "SELECT * FROM sales ORDER BY id OFFSET 0 ROWS FETCH FIRST 10 ROWS ONLY;",
        "SELECT a, b FROM sales WHERE x IN (1, 2) GROUP BY a, b",
        'SELECT "customer" FROM "sales"',
    ]
    BAD = {
        "DELETE FROM sales": "only SELECT",
        "UPDATE sales SET amount = 0": "only SELECT",
        "SELECT 1 FROM sales; DROP TABLE sales": "one statement",
        "SELECT * FROM sales -- hidden": "comments",
        "SELECT * FROM sales /* x */": "comments",
        "SELECT * FROM sales # x": "comments",
        "SELECT * INTO copy FROM sales": "INTO",
        "WITH d AS (DELETE FROM sales RETURNING *) SELECT * FROM d": "DELETE",
        "SELECT * FROM sales FOR UPDATE": "UPDATE",
        "SELECT pg_sleep(5) FROM sales": "pg_sleep",
        "SELECT query_to_xml('delete from sales', true, true, '') FROM sales": "query_to_xml",
        "SELECT * FROM OPENROWSET('x','y') AS t JOIN sales ON 1=1": "openrowset",
        "SELECT * FROM salaries": "not allowed",
        "SELECT * FROM sales JOIN salaries ON 1=1": "not allowed",
        "SELECT * FROM sales, salaries": "JOIN, not commas",
        "SELECT * FROM sales s , salaries x": "JOIN, not commas",
        "SELECT * FROM (SELECT * FROM salaries) t": "not allowed",
        "SELECT 1": "which tables",
        "": "empty",
        "exec sp_who": "only SELECT",
    }

    def test_allowed(self):
        for q in self.OK:
            g.check(q, ALLOW)

    def test_refused_with_reason(self):
        for q, why in self.BAD.items():
            with self.assertRaises(g.SqlRejected, msg=q) as cm:
                g.check(q, ALLOW)
            self.assertIn(why.lower(), str(cm.exception).lower(), q)

    def test_schema_qualified_names(self):
        g.check("SELECT * FROM dbo.sales", {"dbo.sales"})
        g.check("SELECT * FROM dbo.sales", {"sales"})
        with self.assertRaises(g.SqlRejected):
            g.check("SELECT * FROM otherdb.dbo.sales", {"items"})

    def test_limit_and_allowlist_parsing(self):
        self.assertEqual(g.with_limit("SELECT a FROM t", "mssql", 50), "SELECT TOP 50 * FROM (SELECT a FROM t) AS insight_q")
        self.assertTrue(g.with_limit("SELECT a FROM t", "mysql", 10 ** 9).endswith("LIMIT 10000"))
        self.assertEqual(g.parse_allowlist("sales,\n items , dbo.x"), {"sales", "items", "dbo.x"})
        self.assertIsNone(g.parse_allowlist("  "))


def sqlite_db():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE sales (id INTEGER, customer TEXT, region TEXT, amount NUMERIC, sold_on TEXT, item INTEGER);
        CREATE TABLE items (id INTEGER, name TEXT);
        CREATE TABLE salaries (emp TEXT, pay NUMERIC);
        INSERT INTO items VALUES (1,'Water 330ml'),(2,'Water 1.5L');
        INSERT INTO salaries VALUES ('ceo', 99999);
    """)
    rows = [(i, f"C{i % 7}", ["Riyadh", "Jeddah", "Dammam"][i % 3], 100 + i, f"2026-0{1 + i % 6}-01", 1 + i % 2)
            for i in range(1, 1201)]
    c.executemany("INSERT INTO sales VALUES (?,?,?,?,?,?)", rows)
    c.commit()
    return c


class Conn:
    """Wraps sqlite to record rollback/close, like a real driver."""
    def __init__(self, db):
        self.db, self.rolled, self.closed = db, False, False

    def cursor(self):
        return self.db.cursor()

    def rollback(self):
        self.rolled = True

    def close(self):
        self.closed = True


def src(**kw):
    base = dict(name="Plant DB", db_type="PostgreSQL", allowed_tables="sales, items", row_limit=500,
                timeout_seconds=20, enabled=1, allowed_roles="", schema_json="", host="h", port=None,
                database="d", username="u", use_ssl=1)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestRun(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("external_sources", None)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ext_t", HERE.parents[0] / "neotec_insight" / "utils" / "external_sources.py")
        sys.modules["neotec_insight.neotec_insight.utils.sql_guard"] = g
        self.ext = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ext)
        self.db = sqlite_db()
        self.conns = []

    def connect(self, *_):
        c = Conn(self.db)
        self.conns.append(c)
        return c

    def test_real_aggregate_query(self):
        r = self.ext.run(src(), "", "SELECT region, SUM(amount) AS total, COUNT(*) AS n FROM sales GROUP BY region "
                                   "ORDER BY total DESC", connect=self.connect)
        self.assertEqual([c["key"] for c in r["columns"]], ["region", "total", "n"])
        self.assertEqual(len(r["rows"]), 3)
        self.assertEqual(sum(x["n"] for x in r["rows"]), 1200)
        self.assertEqual(r["columns"][0]["type"], "text")
        self.assertIn(r["columns"][1]["type"], ("number", "currency"))
        self.assertTrue(self.conns[-1].rolled and self.conns[-1].closed)

    def test_row_cap_overrides_the_query(self):
        r = self.ext.run(src(row_limit=25), "", "SELECT * FROM sales LIMIT 1000", connect=self.connect)
        self.assertEqual(len(r["rows"]), 25)

    def test_join_through_allowlist(self):
        r = self.ext.run(src(), "", "SELECT i.name, SUM(s.amount) AS total FROM sales s JOIN items i ON i.id = s.item "
                                   "GROUP BY i.name", connect=self.connect)
        self.assertEqual({x["name"] for x in r["rows"]}, {"Water 330ml", "Water 1.5L"})

    def test_refused_before_connecting(self):
        for q in ("SELECT * FROM salaries", "DELETE FROM sales", "SELECT * FROM sales, salaries"):
            with self.assertRaises(g.SqlRejected):
                self.ext.run(src(), "", q, connect=self.connect)
        self.assertEqual(self.conns, [])                 # never reached the database

    def test_value_types_are_json_safe(self):
        self.assertEqual(self.ext._plain(decimal.Decimal("1.50")), 1.5)
        self.assertEqual(self.ext._plain(datetime.date(2026, 1, 2)), "2026-01-02")
        self.assertEqual(self.ext._plain(b"\x00"), "(binary)")

    def test_introspect_respects_allowlist(self):
        class C(Conn):
            def cursor(self):
                cur = types.SimpleNamespace()
                cur.execute = lambda q: None
                cur.fetchall = lambda: [("sales", "id", "int"), ("sales", "amount", "numeric"),
                                        ("salaries", "pay", "numeric"), ("items", "name", "text")]
                return cur
        schema = self.ext.introspect(src(), "", connect=lambda *_: C(None))
        self.assertEqual(set(schema), {"sales", "items"})
        self.assertIn("sales(id int, amount numeric)", self.ext.schema_text(schema))

    def test_dialects(self):
        self.assertEqual(self.ext.dialect("MariaDB / MySQL"), "mysql")
        self.assertEqual(self.ext.dialect("PostgreSQL"), "postgres")
        self.assertEqual(self.ext.dialect("SQL Server"), "mssql")


class Site(unittest.TestCase):
    def setUp(self):
        self.site = ff.install()
        import frappe
        self.frappe = frappe
        self.site.metas["Insight Data Source"]["fields"] = []      # keep the fake simple
        self.site.table("Insight Data Source")["Plant DB"] = {
            "name": "Plant DB", "title": "Plant DB", "enabled": 1, "db_type": "PostgreSQL",
            "allowed_tables": "sales, items", "allowed_roles": "Plant Analyst", "row_limit": 500,
            "timeout_seconds": 20, "schema_json": json.dumps({"sales": [["region", "text"], ["amount", "numeric"]],
                                                             "items": [["id", "int"], ["name", "text"]]})}
        frappe.db.commit()
        frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        from neotec_insight.neotec_insight.api import copilot, sources
        from neotec_insight.neotec_insight.utils import external_sources as ext
        self.cp, self.sources, self.ext = copilot, sources, ext
        sources._password = lambda s: ""
        db = sqlite_db()
        ext._connect = lambda *_: Conn(db)
        copilot._ai_ready = lambda s=None: True

    def tearDown(self):
        ff.uninstall()

    def model(self, *replies):
        it = iter(replies)
        return lambda *a, **k: next(it)


class TestSourceAccess(Site):
    def test_roles(self):
        self.site.user = "analyst@a.co"
        self.site.roles["analyst@a.co"] = {"Plant Analyst"}
        self.assertEqual([s["name"] for s in self.sources.list_sources()], ["Plant DB"])
        self.site.user = "other@a.co"
        self.site.roles["other@a.co"] = {"Accounts User"}
        self.assertEqual(self.sources.list_sources(), [])
        with self.assertRaises(self.frappe.PermissionError):
            self.sources.get_source("Plant DB")

    def test_disabled_source(self):
        self.site.table("Insight Data Source")["Plant DB"]["enabled"] = 0
        with self.assertRaises(self.frappe.ValidationError):
            self.sources.get_source("Plant DB")


class TestAskSource(Site):
    def test_question_to_checked_query_to_answer(self):
        self.cp._chat = self.model(
            json.dumps({"sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC",
                        "view": "bar", "title": "Sales by region"}),
            json.dumps({"answer": "Riyadh leads.", "suggestions": ["By item?"]}))
        r = self.cp.ask("Sales by region", source="Plant DB")
        self.assertTrue(r["ok"])
        self.assertEqual(r["chart"]["type"], "bar")
        self.assertEqual(len(r["rows"]), 3)
        self.assertIn("SUM(amount)", r["sql"])
        self.assertEqual((r["answer"], r["source_name"]), ("Riyadh leads.", "Plant DB"))

    def test_refused_query_gets_one_corrected_retry(self):
        self.cp._chat = self.model(
            json.dumps({"sql": "SELECT * FROM sales, items"}),
            json.dumps({"sql": "SELECT i.name, COUNT(*) AS n FROM sales s JOIN items i ON i.id = s.item GROUP BY i.name",
                        "view": "pie"}),
            json.dumps({"answer": "Two products.", "suggestions": []}))
        r = self.cp.ask("Units by product", source="Plant DB")
        self.assertTrue(r["ok"])
        self.assertIn("JOIN items", r["sql"])

    def test_two_refusals_stop_with_the_reason(self):
        self.cp._chat = self.model(json.dumps({"sql": "SELECT * FROM salaries"}),
                                   json.dumps({"sql": "SELECT pay FROM salaries"}))
        r = self.cp.ask("Show salaries", source="Plant DB")
        self.assertFalse(r["ok"])
        self.assertIn("salaries", r["answer"])

    def test_no_answer_possible(self):
        self.cp._chat = self.model(json.dumps({"sql": None}))
        self.assertFalse(self.cp.ask("Weather in Riyadh", source="Plant DB")["ok"])

    def test_needs_ai(self):
        self.cp._ai_ready = lambda s=None: False
        with self.assertRaises(self.frappe.ValidationError):
            self.cp.ask("Sales by region", source="Plant DB")

    def test_saved_report_is_rechecked_on_every_run(self):
        self.sources.save_source_report(title="Regions", source="Plant DB",
                                        sql="SELECT region, COUNT(*) AS n FROM sales GROUP BY region", view="bar")
        r = self.sources.run_source_report("Regions")
        self.assertEqual(len(r["rows"]), 3)
        self.site.table("Insight Source Report")["Regions"]["sql"] = "SELECT * FROM salaries"   # tampered
        with self.assertRaises(Exception):          # SqlRejected from the package copy of the guard
            self.sources.run_source_report("Regions")
        with self.assertRaises(Exception):
            self.sources.save_source_report(title="Bad", source="Plant DB", sql="DELETE FROM sales")


class TestErpnextDoctypes(unittest.TestCase):
    def test_custom_doctypes_are_allowed_when_readable(self):
        allowed = ["Sales Invoice", "Lab Consumption Entry", "Van Sales Trip"]
        p = core.validate_plan({"tool": "find_records", "args": {"doctype": "lab consumption entry", "request": "last week"}},
                               allowed_doctypes=allowed)
        self.assertEqual(p["args"]["doctype"], "Lab Consumption Entry")
        with self.assertRaises(core.PlanError):
            core.validate_plan({"tool": "find_records", "args": {"doctype": "Salary Slip", "request": "x"}},
                               allowed_doctypes=allowed)

    def test_shortlist_puts_the_meant_doctype_first(self):
        names = ["Sales Invoice", "Van Sales Trip", "Lab Consumption Entry", "Item", "Customer", "Asset"]
        self.assertEqual(core.rank_doctypes("Show lab consumption entries this month", names)[0], "Lab Consumption Entry")
        self.assertEqual(core.rank_doctypes("van sales trips by driver", names)[0], "Van Sales Trip")


if __name__ == "__main__":
    unittest.main()
