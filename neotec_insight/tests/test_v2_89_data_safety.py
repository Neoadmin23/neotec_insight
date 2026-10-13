"""v2.89.0 — data safety: backup covers everything automatically, restores in
dependency order, snapshots, and a grid Save that cannot make figures vanish.

All pure: no site needed."""
import ast
import datetime
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]            # .../neotec_insight (package)
APP = ROOT / "neotec_insight"                           # module folder
REPO = ROOT.parent                                      # app repo root
sys.path.insert(0, str(APP / "utils"))

import allocation_guard as ag  # noqa: E402
import config_backup_registry as reg  # noqa: E402
import snapshot_core as sc  # noqa: E402


def _doctype_folder():
    """[{name, is_single, istable, links}] from the doctype JSONs."""
    out = []
    for folder in sorted((APP / "doctype").iterdir()):
        j = folder / f"{folder.name}.json"
        if not j.exists():
            continue
        d = json.loads(j.read_text())
        links = {f["options"] for f in d["fields"]
                 if f.get("fieldtype") in ("Link", "Table", "Table MultiSelect") and f.get("options")}
        out.append({"name": d["name"], "is_single": bool(d.get("issingle")),
                    "istable": bool(d.get("istable")), "links": links})
    return out


def _deps(all_dt):
    """Module dependencies, following child tables, as backup_engine does live."""
    by = {d["name"]: d for d in all_dt}
    names = {d["name"] for d in all_dt if not d["istable"]}
    deps = {}
    for d in all_dt:
        if d["istable"]:
            continue
        t = set()
        for o in d["links"]:
            if o in names:
                t.add(o)
            elif o in by and by[o]["istable"]:
                t |= {x for x in by[o]["links"] if x in names}
        deps[d["name"]] = t
    return deps


ALL = _doctype_folder()
LIVE = [{"name": d["name"], "is_single": d["is_single"]} for d in ALL if not d["istable"]]
PLAN = reg.build_plan(LIVE, _deps(ALL))
ORDER = [p["doctype"] for p in PLAN]


class TestBackupCoversEverything(unittest.TestCase):
    def test_every_doctype_except_the_snapshot_store_is_in_the_plan(self):
        want = {d["name"] for d in LIVE} - {"Insight Backup Snapshot"}
        self.assertEqual(set(ORDER), want)

    def test_allocation_entries_and_data_areas_are_in(self):
        for dt in ("Insight Allocation Rule", "Insight Allocation Entry", "Insight VAT Adjustment",
                   "Insight Payment Order", "Insight Bank Slip", "Insight Cash Flow Override"):
            self.assertIn(dt, ORDER)

    def test_an_unregistered_new_doctype_is_included_automatically(self):
        plan = reg.build_plan(LIVE + [{"name": "Insight Future Feature", "is_single": False}])
        hit = [p for p in plan if p["doctype"] == "Insight Future Feature"]
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0]["auto"])
        self.assertEqual(hit[0]["area"], reg.AUTO_AREA)

    def test_a_retired_registry_line_does_not_break_the_plan(self):
        plan = reg.build_plan([{"name": "A", "is_single": False}],
                              registry=[{"doctype": "Gone", "area": "x"}, {"doctype": "A", "area": "y"}],
                              excluded={})
        self.assertEqual([p["doctype"] for p in plan], ["A"])

    def test_singles_come_last_and_keep_is_single(self):
        singles = [p for p in PLAN if p["is_single"]]
        self.assertTrue(singles)
        self.assertEqual(PLAN[-len(singles):], singles)

    def test_restore_order_follows_links(self):
        before = lambda a, b: self.assertLess(ORDER.index(a), ORDER.index(b), f"{a} before {b}")
        before("Insight Allocation Rule", "Insight Allocation Entry")
        before("Insight Report Definition", "Insight Allocation Rule")
        before("Insight Budget Book", "Insight Budget Cell")
        before("Insight Cash Flow Line", "Insight Cash Flow Budget")
        before("Insight Cash Flow Line", "Insight Cash Flow Override")
        before("Insight Equity Component", "Insight Equity Movement")

    def test_every_planned_link_target_restores_first(self):
        deps = _deps(ALL)
        for dt in ORDER:
            for target in deps.get(dt, ()):
                if target != dt and target in ORDER:
                    self.assertLess(ORDER.index(target), ORDER.index(dt), f"{target} -> {dt}")


class TestOrdering(unittest.TestCase):
    def test_keeps_order_when_consistent(self):
        self.assertEqual(reg.order_by_dependencies(["a", "b", "c"], {}), ["a", "b", "c"])

    def test_moves_dependency_forward(self):
        self.assertEqual(reg.order_by_dependencies(["entry", "rule"], {"entry": {"rule"}}), ["rule", "entry"])

    def test_self_link_and_cycle_do_not_hang(self):
        out = reg.order_by_dependencies(["a", "b", "c"], {"a": {"a", "b"}, "b": {"a"}})
        self.assertEqual(sorted(out), ["a", "b", "c"])


class TestCountDrops(unittest.TestCase):
    def test_table_emptied_is_reported(self):
        d = reg.count_drops({"Insight Allocation Entry": 84}, {"Insight Allocation Entry": 0})
        self.assertEqual(d, [{"doctype": "Insight Allocation Entry", "before": 84, "after": 0, "lost": 84}])

    def test_small_edits_stay_quiet(self):
        self.assertEqual(reg.count_drops({"Insight Dashboard": 10}, {"Insight Dashboard": 9}), [])
        self.assertEqual(reg.count_drops({"X": 100}, {"X": 80}), [])

    def test_half_gone_is_reported(self):
        self.assertEqual(len(reg.count_drops({"X": 100}, {"X": 40})), 1)

    def test_doctype_missing_after_counts_as_zero(self):
        self.assertEqual(reg.count_drops({"X": 6}, {})[0]["after"], 0)


class TestSnapshotCore(unittest.TestCase):
    BUNDLE = {"version": "2.89.0", "data": {"Insight Allocation Entry": [
        {"name": "a", "rule": "GMO Allocations", "period_month": datetime.date(2026, 1, 1), "driver_value": 8},
        {"name": "b", "rule": "GMO Allocations", "period_month": "2025-12-01", "driver_value": 7},
        {"name": "c", "rule": "Other", "period_month": "2026-02-01", "driver_value": 1}]},
        "counts": {"Insight Allocation Entry": 3}}

    def test_roundtrip_with_dates(self):
        packed, kb = sc.encode(self.BUNDLE)
        back = sc.decode(packed)
        self.assertEqual(back["data"]["Insight Allocation Entry"][0]["period_month"], "2026-01-01")
        self.assertGreater(kb, 0)

    def test_fingerprint_ignores_key_order_and_metadata(self):
        a = {"X": [{"p": 1, "q": 2}]}
        b = {"X": [{"q": 2, "p": 1}]}
        self.assertEqual(sc.fingerprint(a), sc.fingerprint(b))
        self.assertNotEqual(sc.fingerprint(a), sc.fingerprint({"X": [{"p": 1, "q": 3}]}))

    def test_index_counts_per_rule_and_year(self):
        idx = sc.build_index(json.loads(sc.dumps(self.BUNDLE)))
        self.assertEqual(idx["allocation_entries_by_rule"], {"GMO Allocations": 2, "Other": 1})
        self.assertEqual(idx["allocation_entries_by_rule_year"]["GMO Allocations"], {"2026": 1, "2025": 1})

    def test_entries_for_rule_and_year(self):
        b = json.loads(sc.dumps(self.BUNDLE))
        self.assertEqual(len(sc.entries_for_rule(b, "GMO Allocations")), 2)
        self.assertEqual([r["name"] for r in sc.entries_for_rule(b, "GMO Allocations", 2026)], ["a"])

    def test_prune_keeps_newest_and_pinned(self):
        rows = [{"name": f"s{i}", "snapshot_type": "scheduled", "pinned": 0,
                 "creation": f"2026-10-{i:02d}"} for i in range(1, 6)]
        rows[0]["pinned"] = 1                              # oldest, pinned
        gone = sc.to_prune(rows, {"scheduled": 2})
        self.assertEqual(gone, ["s2", "s3"])               # s5, s4 newest kept; s1 pinned

    def test_prune_is_per_type(self):
        rows = [{"name": "m", "snapshot_type": "pre_migrate", "creation": "2026-01-01"},
                {"name": "d", "snapshot_type": "scheduled", "creation": "2026-10-01"}]
        self.assertEqual(sc.to_prune(rows, {"pre_migrate": 1, "scheduled": 1}), [])

    def test_alert_text(self):
        self.assertIn("84 → 0", sc.alert_text(reg.count_drops({"E": 84}, {"E": 0})))


def _row(name, driver=0.0, frozen=0, source="manual", captured_on=None, captured_value=0.0,
         basis="head_count", amount=0.0, budget=0.0, pool=0.0):
    return {"name": name, "driver_value": driver, "is_frozen": frozen, "source": source,
            "captured_on": captured_on, "captured_value": captured_value, "basis": basis,
            "amount": amount, "budget_amount": budget, "manual_pool": pool}


class TestGridSavePlan(unittest.TestCase):
    def plan(self, existing, cells, basis=None, pool=None, remove=None):
        return ag.plan_grid_save(existing, cells, basis or {}, pool or {}, remove or [])

    def ops(self, p, kind):
        return [o for o in p["ops"] if o["op"] == kind]

    def test_frozen_month_is_never_edited(self):
        p = self.plan({("Audit", 1): _row("e1", 8, frozen=1)}, {"Audit": {"1": {"driver": 9}}})
        self.assertEqual(p["counts"]["skip_frozen"], 1)
        self.assertFalse(self.ops(p, "update"))

    def test_frozen_month_is_never_deleted_by_a_blank_cell(self):
        p = self.plan({("Audit", 1): _row("e1", 8, frozen=1)}, {"Audit": {"1": {"driver": 0}}})
        self.assertFalse(self.ops(p, "delete"))
        self.assertEqual(p["counts"]["skip_frozen"], 1)

    def test_frozen_rows_survive_row_removal(self):
        ex = {("Audit", 1): _row("e1", 8, frozen=1), ("Audit", 2): _row("e2", 8)}
        p = self.plan(ex, {}, remove=["Audit"])
        self.assertEqual([o["name"] for o in self.ops(p, "delete")], ["e2"])
        self.assertEqual(p["counts"]["skip_frozen"], 1)

    def test_blank_cell_deletes_open_row(self):
        p = self.plan({("Audit", 3): _row("e3", 8)}, {"Audit": {"3": {"driver": 0}}})
        self.assertEqual(p["counts"]["blank_delete"], 1)

    def test_unchanged_rows_are_not_rewritten(self):
        p = self.plan({("Audit", 1): _row("e1", 8)}, {"Audit": {"1": {"driver": 8}}})
        self.assertEqual(p["counts"]["unchanged"], 1)
        self.assertFalse(self.ops(p, "update"))

    def test_typing_over_a_captured_figure_marks_override(self):
        ex = {("HR", 4): _row("e4", 4, source="employee_headcount", captured_on="2026-05-01", captured_value=4)}
        p = self.plan(ex, {"HR": {"4": {"driver": 5}}})
        self.assertEqual(self.ops(p, "update")[0]["values"]["is_override"], 1)
        self.assertEqual(p["counts"]["overrides"], 1)

    def test_typing_the_captured_value_back_clears_override(self):
        ex = {("HR", 4): _row("e4", 5, source="employee_headcount", captured_on="x", captured_value=4)}
        p = self.plan(ex, {"HR": {"4": {"driver": 4}}})
        self.assertEqual(self.ops(p, "update")[0]["values"]["is_override"], 0)

    def test_manual_row_edit_carries_no_override_flag(self):
        p = self.plan({("A", 1): _row("e1", 3)}, {"A": {"1": {"driver": 4}}})
        self.assertNotIn("is_override", self.ops(p, "update")[0]["values"])

    def test_amount_row_keeps_amount_only(self):
        p = self.plan({}, {"Main": {"1": {"driver": 99, "amount": 45000}}}, basis={"Main": "amount"})
        v = self.ops(p, "insert")[0]["values"]
        self.assertEqual((v["driver_value"], v["amount"], v["basis"]), (0.0, 45000.0, "amount"))

    def test_bulk_confirmation_threshold(self):
        ex = {("A", m): _row(f"e{m}", 1) for m in range(1, 13)}
        ex.update({("B", 1): _row("b1", 1)})
        cells = {"A": {str(m): {"driver": 0} for m in range(1, 13)}, "B": {"1": {"driver": 0}}}
        p = self.plan(ex, cells)
        self.assertEqual(p["counts"]["blank_delete"], 13)
        self.assertTrue(ag.needs_bulk_confirmation(p["counts"]))
        p2 = self.plan(ex, {"A": {str(m): {"driver": 0} for m in range(1, 13)}})
        self.assertFalse(ag.needs_bulk_confirmation(p2["counts"]))

    def test_explicit_removal_needs_no_second_confirmation(self):
        ex = {("A", m): _row(f"e{m}", 1) for m in range(1, 13)}
        ex.update({("B", m): _row(f"b{m}", 1) for m in range(1, 13)})
        p = self.plan(ex, {}, remove=["A", "B"])
        self.assertEqual(p["counts"]["delete"], 24)
        self.assertFalse(ag.needs_bulk_confirmation(p["counts"]))

    def test_stale_grid_detection(self):
        self.assertTrue(ag.is_stale(0, 84))       # grid loaded empty, site holds figures
        self.assertTrue(ag.is_stale("80", 84))
        self.assertFalse(ag.is_stale(84, 84))
        self.assertFalse(ag.is_stale(None, 84))   # old client
        self.assertFalse(ag.is_stale("", 84))


class TestCaptureLeavesAmountRowsAlone(unittest.TestCase):
    def test_amount_basis_row_is_not_captured(self):
        src = (APP / "utils" / "driver_capture.py").read_text()
        tree = ast.parse(src)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "row_action")
        ns = {"flt": lambda x: float(x or 0)}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "x", "exec"), ns)
        self.assertEqual(ns["row_action"]({"basis": "amount", "driver_value": 0}, 3), "amount")
        self.assertEqual(ns["row_action"]({"basis": "head_count", "driver_value": 2}, 3), "update")
        self.assertIn('"amount")', src.split("def commit")[1].split("for row")[1][:400])


class TestWiring(unittest.TestCase):
    def test_hooks_register_snapshots(self):
        h = (ROOT / "hooks.py").read_text()
        self.assertIn("utils.snapshots.before_migrate", h)
        self.assertIn("utils.snapshots.after_migrate", h)
        self.assertIn("utils.snapshots.daily", h)

    def test_patch_listed_and_present(self):
        for line in (ROOT / "patches.txt").read_text().split():
            path = ROOT.parent / Path(*line.split(".")).with_suffix(".py")
            self.assertTrue(path.exists(), line)
        self.assertIn("v2_89.install_snapshot_store", (ROOT / "patches.txt").read_text())

    def test_snapshot_doctype(self):
        d = json.loads((APP / "doctype" / "insight_backup_snapshot" / "insight_backup_snapshot.json").read_text())
        self.assertEqual(d["module"], "Neotec Insight")
        self.assertEqual(d.get("track_changes"), 0)
        self.assertIn("payload", [f["fieldname"] for f in d["fields"]])

    def test_entries_keep_version_history(self):
        d = json.loads((APP / "doctype" / "insight_allocation_entry" / "insight_allocation_entry.json").read_text())
        self.assertEqual(d.get("track_changes"), 1)

    def test_grid_sends_loaded_count_and_ignores_late_replies(self):
        src = (REPO / "reportManager" / "src" / "features" / "allocation" / "AllocationApp.tsx").read_text()
        self.assertIn("seq !== gridSeq.current", src)
        self.assertIn("seq === runSeq.current", src)
        self.assertIn(r"/^\d{4}$/.test(v)", src)
        api = (REPO / "reportManager" / "src" / "utils" / "api.ts").read_text()
        self.assertIn("loaded_count", api)


class TestEveryFrontendCallHasAnEndpoint(unittest.TestCase):
    """Health check kept as a test: every call('module.fn') in the frontend
    names a @frappe.whitelist() function in api/<module>.py."""

    def test_calls_resolve(self):
        whitelisted = {}
        for f in (APP / "api").glob("*.py"):
            tree = ast.parse(f.read_text())
            names = set()
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and any(
                        "whitelist" in ast.unparse(d) for d in n.decorator_list):
                    names.add(n.name)
            whitelisted[f.stem] = names
        calls = set()
        for f in (REPO / "reportManager" / "src").rglob("*.ts*"):
            calls |= set(re.findall(r"call<[^>]*>\(\s*'([a-z_]+\.[a-z_0-9]+)'", f.read_text()))
            calls |= set(re.findall(r"call\(\s*'([a-z_]+\.[a-z_0-9]+)'", f.read_text()))
        self.assertGreater(len(calls), 200)
        missing = sorted(c for c in calls
                         if c.split(".")[1] not in whitelisted.get(c.split(".")[0], set()))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()


class TestModalClassesExist(unittest.TestCase):
    """v2.89.4 — the Diagnose window used class names that were not in the
    stylesheet and drew transparent over the page. Every class used by the
    allocation and backup windows must exist in the CSS."""

    FILES = ["features/allocation/DiagnoseModal.tsx", "features/allocation/ImportHeadCountsModal.tsx",
             "features/allocation/EntriesRecoveryModal.tsx", "features/shell/ConfigBackupModal.tsx",
             "features/shell/RecoverPanel.tsx"]

    def test_classes_defined(self):
        src = REPO / "reportManager" / "src"
        css = "".join(p.read_text() for p in src.rglob("*.css"))
        missing = []
        for f in self.FILES:
            text = (src / f).read_text()
            for lit in re.findall(r'className="([^"]+)"', text) + re.findall(r"className=\{'([^']+)'", text):
                for cls in lit.split():
                    if not re.search(r"\." + re.escape(cls) + r"(?![\w-])", css):
                        missing.append(f"{f}: {cls}")
        self.assertEqual(sorted(set(missing)), [])
