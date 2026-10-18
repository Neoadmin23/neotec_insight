"""v2.93.0 — one branch runs on Frappe v15 and v16.

Each test guards one v16 change from frappe/frappe's "Migrating to version 16"
and its query-builder guide, so a later edit cannot quietly reintroduce it."""
import ast
import re
import sys
import types
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "neotec_insight"
REPO = ROOT.parent
QUERY_CALLS = ("get_all", "get_list", "db.get_value", "db.get_values", "qb.get_query")


def _calls():
    for p in APP.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        tree = ast.parse(p.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(QUERY_CALLS):
                yield p, n, parents.get(n)


class TestQueryBuilderSyntax(unittest.TestCase):
    """v16 runs get_all/get_list through the query builder: SQL functions,
    'distinct x', backticks and raw aliases in field strings are rejected."""

    def test_no_sql_in_field_strings(self):
        bad = []
        for p, n, _ in _calls():
            for kw in n.keywords:
                if kw.arg in ("fields", "pluck", "order_by", "group_by"):
                    v = ast.unparse(kw.value)
                    if re.search(r"\b(count|sum|min|max|avg|ifnull|coalesce|concat|year|month)\s*\(|\bdistinct\s|`| as ", v, re.I):
                        bad.append(f"{p.name}:{n.lineno}: {kw.arg}={v[:80]}")
        self.assertEqual(bad, [])

    def test_no_function_keys_in_filters(self):
        bad = []
        for p, n, _ in _calls():
            for kw in n.keywords:
                if kw.arg == "filters" and re.search(r"['\"](ifnull|coalesce|timestamp)\(", ast.unparse(kw.value), re.I):
                    bad.append(f"{p.name}:{n.lineno}")
        self.assertEqual(bad, [])


class TestImplicitOrder(unittest.TestCase):
    """v16 changed the implicit order from `modified desc` to `creation desc`.
    Any read that takes 'the first row' must say which first row it means."""

    def test_first_row_reads_have_an_order(self):
        bad = []
        for p, n, parent in _calls():
            if not ast.unparse(n.func).endswith(("get_all", "get_list")):
                continue
            kws = {k.arg: ast.unparse(k.value) for k in n.keywords}
            limit1 = (kws.get("limit_page_length") or kws.get("limit") or kws.get("page_length")) == "1"
            first = isinstance(parent, ast.Subscript) and ast.unparse(parent.slice) == "0"
            if (limit1 or first) and "order_by" not in kws:
                bad.append(f"{p.name}:{n.lineno}")
        self.assertEqual(bad, [])


class TestDeskRoute(unittest.TestCase):
    """v16 moved desk from /app to /desk: no hard-coded /app links remain."""

    def test_python_has_no_hardcoded_app_links(self):
        bad = []
        for p in list(APP.rglob("*.py")) + [ROOT / "install.py"]:
            if "__pycache__" in p.parts or p.name == "compat.py":
                continue
            for i, line in enumerate(p.read_text().split("\n"), 1):
                code = line.split("#", 1)[0]
                if re.search(r"""["']/app(/|["'?])""", code):
                    bad.append(f"{p.name}:{i}")
        self.assertEqual(bad, [])

    def test_frontend_has_no_hardcoded_app_links(self):
        bad = []
        for p in (REPO / "reportManager" / "src").rglob("*.ts*"):
            if p.name == "desk.ts":
                continue
            for i, line in enumerate(p.read_text().split("\n"), 1):
                if re.search(r"""[`'"]/app(/|[`'"?])""", line):
                    bad.append(f"{p.name}:{i}")
        self.assertEqual(bad, [])


class TestCompatHelpers(unittest.TestCase):
    def load(self, version):
        sys.modules["frappe"] = types.SimpleNamespace(__version__=version)
        import importlib.util
        spec = importlib.util.spec_from_file_location("compat_t", APP / "utils" / "compat.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def tearDown(self):
        sys.modules.pop("frappe", None)

    def test_prefix_by_version(self):
        for v, want in (("15.122.0", "/app"), ("16.0.0", "/desk"), ("16.0.0-dev", "/desk"), ("17.1.0", "/desk")):
            m = self.load(v)
            self.assertEqual(m.desk_prefix(), want, v)
            self.assertEqual(m.desk_url("query-report/General Ledger"), f"{want}/query-report/General Ledger")

    def test_stored_links_follow_the_running_version(self):
        m16 = self.load("16.0.0")
        self.assertEqual(m16.to_desk_path("/app/general-ledger"), "/desk/general-ledger")
        self.assertEqual(m16.to_desk_path("/app"), "/desk")
        self.assertEqual(m16.to_desk_path("/app?x=1"), "/desk?x=1")
        self.assertEqual(m16.to_desk_path("/insight"), "/insight")
        self.assertEqual(m16.to_desk_path("/apps"), "/apps")
        self.assertEqual(m16.to_desk_path("https://x.com/app/a"), "https://x.com/app/a")
        m15 = self.load("15.122.0")
        self.assertEqual(m15.to_desk_path("/desk/account"), "/app/account")

    def test_with_aliases(self):
        m = self.load("16.0.0")
        rows = m.with_aliases([{"name": "A", "customer_name": "Acme"}], {"label": "customer_name", "value": "name"})
        self.assertEqual(rows[0]["label"], "Acme")
        self.assertEqual(rows[0]["value"], "A")


class TestDeclaredSupport(unittest.TestCase):
    def test_pyproject_allows_15_and_16_only(self):
        txt = (REPO / "pyproject.toml").read_text()
        self.assertIn('frappe = ">=15.0.0,<17.0.0"', txt)

    def test_has_permission_hook_returns_bools(self):
        src = (APP / "doctype" / "insight_report_definition" / "insight_report_definition.py").read_text()
        fn = src[src.index("def has_permission("):src.index("def can_manage(")]
        for ret in re.findall(r"^\s+return (.+)$", fn, re.M):
            self.assertTrue(ret.startswith("bool("), ret)

    def test_doc_event_hooks_never_commit(self):
        hooks = (ROOT / "hooks.py").read_text()
        targets = set(re.findall(r'"(neotec_insight\.[\w.]+)"', hooks[hooks.index("doc_events"):hooks.index("}", hooks.index('"Account"'))]))
        for t in targets:
            mod, fn = t.rsplit(".", 1)
            src = (REPO / Path(*mod.split("."))).with_suffix(".py").read_text()
            node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == fn)
            self.assertNotIn("commit", ast.unparse(node), t)

    def test_compiles_without_warnings(self):
        problems = []
        for p in ROOT.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                try:
                    compile(p.read_text(), str(p), "exec")
                except (SyntaxError, SyntaxWarning, DeprecationWarning) as e:
                    problems.append(f"{p.name}: {e}")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
