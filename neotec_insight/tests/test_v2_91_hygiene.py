"""v2.91.0 — code hygiene that protects behaviour.

* Every `from neotec_insight… import name` resolves to a module and a name
  that exist. Two imports of a module that never existed sat inside
  try/except for many releases, so custom accounting-dimension labels always
  fell back to a title-cased field name, and nothing failed.
* No behaviour-relevant Ruff findings in app code (closures over loop
  variables, unused variables, shadowed imports, unchecked zip lengths)."""
import ast
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # package dir
REPO = ROOT.parent


def _modules():
    out = {}
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        name = ".".join(p.relative_to(REPO).with_suffix("").parts)
        if name.endswith(".__init__"):
            name = name[: -len(".__init__")]
        out[name] = p
    return out


def _names(path: Path, mods: dict, modname: str) -> set:
    t = ast.parse(path.read_text())
    names = set()
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for tg in n.targets:
                names |= {x.id for x in ast.walk(tg) if isinstance(x, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names}
    names |= {m.rsplit(".", 1)[1] for m in mods if "." in m and m.rsplit(".", 1)[0] == modname}
    return names


class TestImportsResolve(unittest.TestCase):
    def test_every_in_app_import_resolves(self):
        mods = _modules()
        bad = []
        for name, p in mods.items():
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").startswith("neotec_insight"):
                    if n.module not in mods:
                        bad.append(f"{p.name}: no module {n.module}")
                        continue
                    have = _names(mods[n.module], mods, n.module)
                    bad += [f"{p.name}: {n.module}.{a.name}" for a in n.names if a.name != "*" and a.name not in have]
        self.assertEqual(bad, [])


@unittest.skipUnless(shutil.which("ruff"), "ruff not installed")
class TestRuffBehaviourRules(unittest.TestCase):
    def test_app_code_is_clean(self):
        r = subprocess.run(["ruff", "check", str(ROOT / "neotec_insight"), "--select",
                            "B023,F841,F402,B007,B905,F401,F811,F821,F823", "--output-format", "concise",
                            "--no-cache"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class TestReportSplitKeepsEveryEndpoint(unittest.TestCase):
    """v2.91.0 moves areas out of api/report.py. The screen, desk scripts and
    saved links call `neotec_insight.neotec_insight.api.report.<name>`, so
    every endpoint report.py exposed in v2.90.0 must still be reachable there
    — defined in report.py or re-exported from the module it moved to — with
    the same whitelist decorator (same HTTP methods)."""

    def test_all_v2_90_endpoints_resolve_with_same_methods(self):
        import json
        want = json.loads((Path(__file__).parent / "fixtures" / "report_endpoints_v2_90.json").read_text())
        api = ROOT / "neotec_insight" / "api"
        rep = ast.parse((api / "report.py").read_text())
        local = {n.name: n for n in rep.body if isinstance(n, ast.FunctionDef)}
        reexported = {}
        for n in rep.body:
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("neotec_insight.neotec_insight.api."):
                for a in n.names:
                    reexported[a.asname or a.name] = (n.module.rsplit(".", 1)[1], a.name)
        missing, changed = [], []
        for name, dec in want.items():
            if name in local:
                fn = local[name]
            elif name in reexported:
                mod, real = reexported[name]
                tree = ast.parse((api / f"{mod}.py").read_text())
                fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == real), None)
                if fn is None:
                    missing.append(name)
                    continue
            else:
                missing.append(name)
                continue
            got = next((ast.unparse(d) for d in fn.decorator_list if "whitelist" in ast.unparse(d)), None)
            if got != dec:
                changed.append(f"{name}: {dec} -> {got}")
        self.assertEqual((missing, changed), ([], []))
        self.assertEqual(len(want), 97)


SPLIT = {
    "report_shared": ["_require_read", "_require_write", "_resolve_report_doc", "_sanitise_dimension_filters",
                      "_check_group_view_access", "_check_edit_permission", "_all_valid_dimension_fieldnames"],
    "saved_dashboards": ["list_dashboards", "get_dashboard", "save_dashboard", "delete_dashboard"],
    "liquidity": ["get_liquidity", "get_sensitivity_scenario", "get_sensitivity_tornado"],
    "analytics": ["get_financial_ratios", "get_rolling_12", "get_fragility_radar", "get_multi_year_trend"],
    "variance_notes": ["list_variance_notes", "save_variance_note"],
    "group_view": ["list_group_companies", "get_group_dashboard"],
    "equity": ["list_equity_components", "list_equity_movement_types", "get_equity_movement",
               "save_equity_movement", "delete_equity_movement"],
    "brand": ["company_brand", "set_company_logo"],
    "config_backup": ["export_configuration", "import_configuration", "config_areas"],
}


class TestSplitImportsInAnyOrder(unittest.TestCase):
    """v2.92.0 — each module carved out of report.py imports cleanly on its
    own (no cycle through report.py), and report.<name> is the very same
    function object, so Frappe resolves the old address to the same
    whitelisted callable."""

    def test_each_module_first_then_report(self):
        import importlib
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.path.insert(0, str(REPO))
        import _fakefrappe as ff
        for mod, names in SPLIT.items():
            ff.install()
            try:
                m = importlib.import_module(f"neotec_insight.neotec_insight.api.{mod}")
                r = importlib.import_module("neotec_insight.neotec_insight.api.report")
                for n in names:
                    self.assertIs(getattr(r, n), getattr(m, n), f"{mod}.{n}")
            finally:
                ff.uninstall()

    def test_report_first_then_each_module(self):
        import importlib
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.path.insert(0, str(REPO))
        import _fakefrappe as ff
        ff.install()
        try:
            r = importlib.import_module("neotec_insight.neotec_insight.api.report")
            for mod, names in SPLIT.items():
                m = importlib.import_module(f"neotec_insight.neotec_insight.api.{mod}")
                for n in names:
                    self.assertIs(getattr(r, n), getattr(m, n), f"{mod}.{n}")
        finally:
            ff.uninstall()

    def test_shared_module_never_imports_report(self):
        src = (ROOT / "neotec_insight" / "api" / "report_shared.py").read_text()
        self.assertNotIn("api.report import", src)
        self.assertNotIn("from .report", src)
