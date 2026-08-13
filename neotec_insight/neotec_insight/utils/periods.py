from __future__ import annotations

# Calendar month abbreviations. Indexed 0..11 in calendar order (0=Jan).
# When the caller has a company context, use `calendar_month_for_fy_month`
# from utils/fiscal_year to translate FY-month index → calendar index.
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

ALLOWED_GRANULARITIES = {
    "month",
    "quarter",
    "half",
    "ytd",
    "month_quarter",
    "month_half",
    "quarter_half",
    "month_quarter_half",
    "quarter_frame",
    "quarter_ytd",
}


def _month_label_for_fy_index(company: str | None, fy_idx: int, fy_start_month_override: int | None = None) -> str:
    """Translate an FY-month index (0 = first month of the company's FY) to
    a calendar month abbreviation. Defensive — falls back to Jan-start if
    the helper is unavailable for any reason.

    v1.9.60 — when `fy_start_month_override` is provided, labels reflect
    the override calendar rather than the company's configured one. Used
    for group-reporting views.
    """
    # v1.9.60 — override path: no company lookup needed when caller is
    # explicit about which calendar to use.
    if fy_start_month_override is not None:
        try:
            m = int(fy_start_month_override)
            if 1 <= m <= 12:
                cal_idx = (m - 1 + fy_idx) % 12
                return MONTHS[cal_idx]
        except (TypeError, ValueError):
            pass
    if not company:
        # No company context — assume Jan-start (legacy behaviour).
        return MONTHS[fy_idx % 12]
    try:
        from neotec_insight.neotec_insight.utils.fiscal_year import calendar_month_for_fy_month
        cal_m = calendar_month_for_fy_month(company, fy_idx)
        return MONTHS[cal_m - 1]
    except Exception:
        return MONTHS[fy_idx % 12]


def build_period_groups(month_from: int, month_to: int, granularity: str, company: str | None = None, fy_start_month_override: int | None = None, sel_from: int | None = None, sel_to: int | None = None) -> dict:
    """Build the tiered period structure for a given granularity choice.

    Granularity strings:
      - "month"             — monthly columns only
      - "quarter"           — quarter rollups only
      - "half"              — half-yearly rollups only
      - "ytd"               — single YTD column
      - "month_quarter"     — month columns AND quarter rollups
      - "month_half"        — month columns AND half-yearly rollups
      - "quarter_half"      — quarter columns AND half-yearly rollups
      - "month_quarter_half"— all three tiers

    Returns:
        {
          "groups": [
            {"tier": "month",   "periods": [{"key", "label", "months": [int], "gran"}, ...]},
            {"tier": "quarter", "periods": [...]},
            ...
          ],
          "months": [int, ...],   # the full month range
          "granularity": str,
        }

    Each period's `months` list contains zero-based month indices (0=Jan, 11=Dec).
    A quarter or half-year period only includes the months that fall inside the
    user's From/To window — partial quarters are allowed by design.
    """
    granularity = (granularity or "month").strip()
    # v2.38.3 — quarter_frame is shaped client-side; the builder must pass the
    # string through (previously normalised to "month", which broke the frame).
    if granularity not in ALLOWED_GRANULARITIES:
        granularity = "month"

    if month_from < 0:
        month_from = 0
    if month_to > 11:
        month_to = 11
    if month_to < month_from:
        month_to = month_from

    months = list(range(month_from, month_to + 1))
    groups: list[dict] = []

    # v2.38.4 — quarter frame: the authoritative period_order for the grid.
    # All four quarter totals always present; the user's SELECTED months
    # (sel_from..sel_to) expand in place inside their quarters; quarters
    # entirely after the selection carry future=True so the Actual column can
    # render Budget or blank. Jan–Mar → Jan Feb Mar Q1 · Apr–Jun →
    # Q1 Apr May Jun Q2 · Jul–Sep → Q1 Q2 Jul Aug Sep Q3 · Oct–Dec →
    # Q1 Q2 Q3 Oct Nov Dec Q4.
    if granularity == "quarter_frame":
        sf = month_from if sel_from is None else max(0, min(11, int(sel_from)))
        st = month_to if sel_to is None else max(0, min(11, int(sel_to)))
        if st < sf:
            st = sf
        month_periods: list[dict] = []
        quarter_periods: list[dict] = []
        order: list[dict] = []
        for q in range(4):
            qm = [q * 3, q * 3 + 1, q * 3 + 2]
            for m in [x for x in qm if sf <= x <= st]:
                mp = {"key": f"m{m}", "label": _month_label_for_fy_index(company, m, fy_start_month_override),
                      "months": [m], "gran": "month", "tier": "month"}
                month_periods.append(mp)
                order.append(mp)
            qp = {"key": f"q{q}", "label": f"Q{q + 1}", "months": qm, "gran": "quarter",
                  "tier": "total", "future": q * 3 > st}
            quarter_periods.append(qp)
            order.append(qp)
        if month_periods:
            groups.append({"tier": "month", "periods": month_periods})
        groups.append({"tier": "quarter", "periods": quarter_periods})
        return {
            "groups": groups,
            "months": list(range(0, 12)),
            "granularity": "quarter_frame",
            "period_order": order,
        }

    wants_month = "month" in granularity
    wants_quarter = "quarter" in granularity
    wants_half = "half" in granularity
    wants_ytd = granularity == "ytd"

    if wants_month:
        groups.append(
            {
                "tier": "month",
                "periods": [
                    {"key": f"m{m}", "label": _month_label_for_fy_index(company, m, fy_start_month_override=fy_start_month_override), "months": [m], "gran": "month"}
                    for m in months
                ],
            }
        )

    if wants_quarter:
        qs: dict[int, list[int]] = {}
        for m in months:
            qs.setdefault(m // 3, []).append(m)
        periods = [
            {"key": f"q{q}", "label": f"Q{q + 1}", "months": qs[q], "gran": "quarter"}
            for q in sorted(qs.keys())
        ]
        if periods:
            groups.append({"tier": "quarter", "periods": periods})

    if wants_half:
        hs: dict[int, list[int]] = {}
        for m in months:
            hs.setdefault(0 if m < 6 else 1, []).append(m)
        periods = [
            {"key": f"h{h}", "label": "H1" if h == 0 else "H2", "months": hs[h], "gran": "half"}
            for h in sorted(hs.keys())
        ]
        if periods:
            groups.append({"tier": "half", "periods": periods})

    if wants_ytd:
        groups.append(
            {
                "tier": "ytd",
                "periods": [
                    {
                        "key": "ytd",
                        "label": f"YTD {_month_label_for_fy_index(company, month_from, fy_start_month_override=fy_start_month_override)}–{_month_label_for_fy_index(company, month_to, fy_start_month_override=fy_start_month_override)}",
                        "months": months,
                        "gran": "ytd",
                    }
                ],
            }
        )

    # Build an interleaved period order — months first, then Q after their last
    # month, then H after Jun/Dec, then YTD at the very end. This is what the
    # frontend uses to render columns left-to-right in management-P&L style:
    #   Jan, Feb, Mar, Q1, Apr, May, Jun, Q2, H1, Jul, Aug, Sep, Q3, ...
    # When a tier is disabled by granularity, its periods are absent from the
    # interleave (e.g. quarter_half granularity drops the monthlies entirely).
    period_order = _build_interleaved_order(groups, wants_month, wants_quarter, wants_half, wants_ytd)

    return {
        "groups": groups,
        "months": months,
        "granularity": granularity,
        "period_order": period_order,
    }


def _build_interleaved_order(
    groups: list[dict],
    wants_month: bool,
    wants_quarter: bool,
    wants_half: bool,
    wants_ytd: bool,
) -> list[dict]:
    """Flatten the groups into one interleaved left-to-right column sequence.

    Order rules (matching the user-specified layout):
      Monthly:                M1, M2, ..., M12, Yearly
      Monthly + Quarterly:    M1, M2, M3, Q1, M4, M5, M6, Q2, M7, ..., M12, Q4, Yearly
      Monthly + Q + Half:     M1, M2, M3, Q1,
                              M4, M5, M6, HY1, Q2,            <- HY1 BEFORE Q2
                              M7, M8, M9, Q3,
                              M10, M11, M12, Q4, HY2,         <- HY2 AFTER Q4
                              Yearly

    The asymmetric HY placement (mid-year before Q2, end-of-year after Q4) is
    a deliberate management-P&L convention.
    """
    quarter_by_last_month: dict[int, dict] = {}
    half_by_last_month: dict[int, dict] = {}

    for g in groups:
        if g["tier"] == "quarter":
            for p in g["periods"]:
                quarter_by_last_month[max(p["months"])] = p
        elif g["tier"] == "half":
            for p in g["periods"]:
                half_by_last_month[max(p["months"])] = p

    monthly_periods: list[dict] = []
    for g in groups:
        if g["tier"] == "month":
            monthly_periods = list(g["periods"])
            break

    out: list[dict] = []

    if wants_month and monthly_periods:
        for p in monthly_periods:
            out.append({"tier": "month", **p})
            last_m = p["months"][-1]
            # On Jun (mid-year boundary): HY1 emits BEFORE Q2.
            # On Dec (end-of-year boundary): HY2 emits AFTER Q4.
            # Other quarter boundaries (Mar, Sep): just Q.
            if last_m == 5:  # Jun
                if wants_half and last_m in half_by_last_month:
                    out.append({"tier": "half", **half_by_last_month[last_m]})
                if wants_quarter and last_m in quarter_by_last_month:
                    out.append({"tier": "quarter", **quarter_by_last_month[last_m]})
            elif last_m == 11:  # Dec
                if wants_quarter and last_m in quarter_by_last_month:
                    out.append({"tier": "quarter", **quarter_by_last_month[last_m]})
                if wants_half and last_m in half_by_last_month:
                    out.append({"tier": "half", **half_by_last_month[last_m]})
            else:
                if wants_quarter and last_m in quarter_by_last_month:
                    out.append({"tier": "quarter", **quarter_by_last_month[last_m]})
                # Lone halves on non-quarter-boundary months (e.g. partial range
                # ending in a non-Jun/Dec month) still emit so users don't lose them.
                if wants_half and last_m in half_by_last_month:
                    out.append({"tier": "half", **half_by_last_month[last_m]})
    elif wants_quarter:
        # Months disabled but quarters wanted — emit quarters in order.
        # Halves emit before Q on Jun, after Q on Dec, to match the monthly rule.
        for g in groups:
            if g["tier"] != "quarter":
                continue
            for p in g["periods"]:
                last_m = p["months"][-1]
                if last_m == 5 and wants_half and last_m in half_by_last_month:
                    out.append({"tier": "half", **half_by_last_month[last_m]})
                out.append({"tier": "quarter", **p})
                if last_m == 11 and wants_half and last_m in half_by_last_month:
                    out.append({"tier": "half", **half_by_last_month[last_m]})
    elif wants_half:
        for g in groups:
            if g["tier"] == "half":
                for p in g["periods"]:
                    out.append({"tier": "half", **p})

    if wants_ytd:
        for g in groups:
            if g["tier"] == "ytd":
                for p in g["periods"]:
                    out.append({"tier": "ytd", **p})

    return out
