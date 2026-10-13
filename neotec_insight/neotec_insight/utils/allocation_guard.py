"""What a Save on Allocation > Data entry is allowed to do (v2.89.0). Pure.

save_grid used to apply the grid blindly: a blank cell deleted the stored
row, frozen months were overwritten or deleted like any other, a figure
typed over a captured head count left no trace that it had been overridden,
and nothing stopped a stale or half-loaded grid being saved over the real
figures. Every one of those can make head counts vanish without anyone
deciding they should. This module decides, row by row, and save_grid only
carries the plan out.

Rules
  * Frozen rows are never changed or deleted by a grid save. They are
    reported back as skipped.
  * A blank cell deletes a stored row only as part of a plan the caller can
    see; more than BULK_BLANK_LIMIT such deletions need explicit confirmation.
  * A value typed over a captured head count sets is_override, so the next
    capture keeps it (and the difference stays visible); typing the captured
    value back clears it.
  * Rows whose values did not change are not rewritten (no needless versions).
"""

from __future__ import annotations

BULK_BLANK_LIMIT = 12   # one full cost-centre row; more needs a confirmation


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _on(v) -> bool:
    """A Check value, whatever form it arrives in (1, "1", True, "0", None)."""
    try:
        return bool(int(float(v or 0)))
    except (TypeError, ValueError):
        return bool(v)


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def plan_grid_save(existing: dict, cells: dict, basis: dict, manual_pool: dict,
                   remove: list) -> dict:
    """existing: {(cc, month): row} where row has name, is_frozen, source,
    captured_on, captured_value, basis, driver_value, amount, budget_amount,
    manual_pool. Returns {"ops": [...], "counts": {...}}."""
    ops: list[dict] = []
    removed = set(remove or [])

    for (cc, m), row in sorted(existing.items()):
        if cc in removed:
            ops.append({"op": "skip_frozen" if _on(row.get("is_frozen")) else "delete",
                        "cc": cc, "m": m, "name": row["name"], "why": "removed"})

    for cc, months in (cells or {}).items():
        if cc in removed:
            continue
        cc_basis = (basis or {}).get(cc) or "head_count"
        for m_str, vals in (months or {}).items():
            try:
                m = int(m_str)
            except (TypeError, ValueError):
                continue
            if not 1 <= m <= 12:
                continue
            vals = vals or {}
            driver = _f(vals.get("driver")) if cc_basis == "head_count" else 0.0
            amount = _f(vals.get("amount")) if cc_basis == "amount" else 0.0
            budget = _f(vals.get("budget"))
            pool = _f((manual_pool or {}).get(str(m), (manual_pool or {}).get(m, 0)))
            row = existing.get((cc, m))
            blank = not driver and not amount and not pool and not budget

            if row is None:
                if not blank:
                    ops.append({"op": "insert", "cc": cc, "m": m, "values": {
                        "basis": cc_basis, "driver_value": driver, "amount": amount,
                        "budget_amount": budget, "manual_pool": pool}})
                continue

            same = ((row.get("basis") or "head_count") == cc_basis
                    and _same(_f(row.get("driver_value")), driver)
                    and _same(_f(row.get("amount")), amount)
                    and _same(_f(row.get("budget_amount")), budget)
                    and _same(_f(row.get("manual_pool")), pool))
            if same:
                ops.append({"op": "unchanged", "cc": cc, "m": m, "name": row["name"]})
                continue
            if _on(row.get("is_frozen")):
                ops.append({"op": "skip_frozen", "cc": cc, "m": m, "name": row["name"],
                            "why": "blank" if blank else "edit"})
                continue
            if blank:
                ops.append({"op": "delete", "cc": cc, "m": m, "name": row["name"], "why": "blank"})
                continue
            values = {"basis": cc_basis, "driver_value": driver, "amount": amount,
                      "budget_amount": budget, "manual_pool": pool}
            captured = bool(row.get("captured_on")) or (row.get("source") or "manual") != "manual"
            if captured and cc_basis == "head_count":
                values["is_override"] = 0 if _same(_f(row.get("captured_value")), driver) else 1
            ops.append({"op": "update", "cc": cc, "m": m, "name": row["name"], "values": values})

    counts = {k: 0 for k in ("insert", "update", "delete", "unchanged", "skip_frozen")}
    for o in ops:
        counts[o["op"]] += 1
    counts["blank_delete"] = sum(1 for o in ops if o["op"] == "delete" and o.get("why") == "blank")
    counts["overrides"] = sum(1 for o in ops if o["op"] == "update"
                              and (o.get("values") or {}).get("is_override") == 1)
    return {"ops": ops, "counts": counts}


def needs_bulk_confirmation(counts: dict, limit: int = BULK_BLANK_LIMIT) -> bool:
    """Deletions caused by blank cells beyond one row's worth must be confirmed.
    Explicit row removals were already confirmed in the grid."""
    return int(counts.get("blank_delete") or 0) > limit


def is_stale(loaded_count, stored_count: int) -> bool:
    """The grid was loaded when the rule-year held a different number of
    stored rows than it does now (another save, a capture, a restore — or a
    grid that loaded empty). Saving it would write over figures the user has
    not seen. None = old client that does not send the count: not checked."""
    if loaded_count is None or loaded_count == "":
        return False
    try:
        return int(loaded_count) != int(stored_count)
    except (TypeError, ValueError):
        return False
