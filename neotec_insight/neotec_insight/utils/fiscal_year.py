"""Fiscal year boundary resolution (v1.9.59).

Every date filter in Insight passes through one of these helpers so the
engine adapts cleanly to any fiscal-year-start month. KSA: Jan-start.
India: April-start. Australia: July-start. UK companies: any.

The contract:
  - All internal month indexing is 0-based "FY-month": 0 = first month of
    the company's fiscal year, regardless of calendar position.
  - All SQL date filters use absolute `posting_date BETWEEN start AND end`,
    NEVER `YEAR(posting_date) = fy AND MONTH(posting_date) BETWEEN ...`.
  - Period structures (Q1, Q2, H1, YTD) are in FY-month terms — Q1 is
    always the first three months of the fiscal year for that company.
  - Frontend renders month labels by translating FY-month → calendar
    month name (Jan, Feb, ...) using `calendar_month_for_fy_month`.

Source of truth: ERPNext's `Company.year_start_date` field.
Fallback: ERPNext's `Fiscal Year` DocType (per-FY records like 2024-2025).
Final fallback: calendar Jan 1 of the requested year.
"""
from __future__ import annotations

import datetime
from typing import Tuple

import frappe
from frappe.utils import cint


# Cache the resolved fiscal-year start month per company so we don't hit the
# DB for every report run. Cleared if the admin changes the company's year
# start in ERPNext (which is rare).
_company_fy_start_month_cache: dict[str, int] = {}


def get_company_fy_start_month(company: str | None, override: int | None = None) -> int:
    """Returns the calendar month (1-12) on which `company`'s fiscal year
    begins. Defaults to 1 (January) if company is missing, has no
    year_start_date set, or the field is unreadable.

    Reads from `Company.year_start_date`. ERPNext stores this as a date
    (e.g. 2024-04-01); we extract just the month component because we don't
    care which YEAR the company was founded — only which month their FY
    starts on.

    v1.9.60 — `override` allows the caller to bypass the company's
    configured FY-start month for a single report run. Use case: a Saudi
    subsidiary of an Indian parent runs its local statutory reports on
    its configured KSA Jan-start calendar, but the same data sliced as
    Apr-Mar for group reporting to the Indian parent. Override is a
    calendar month 1..12; when set, the company's stored configuration
    is ignored for this resolution. The cache is bypassed so an
    override-active run never poisons the cache.
    """
    # v1.9.60 — explicit override wins, no caching (override is per-run).
    if override is not None:
        try:
            m = int(override)
            if 1 <= m <= 12:
                return m
        except (TypeError, ValueError):
            pass
    if not company:
        return 1
    if company in _company_fy_start_month_cache:
        return _company_fy_start_month_cache[company]
    try:
        yr_start = frappe.db.get_value("Company", company, "year_start_date")
        if yr_start:
            if isinstance(yr_start, str):
                yr_start = datetime.date.fromisoformat(yr_start[:10])
            m = int(yr_start.month)
            if 1 <= m <= 12:
                _company_fy_start_month_cache[company] = m
                return m
    except Exception:
        pass
    _company_fy_start_month_cache[company] = 1
    return 1


def clear_company_fy_cache(company: str | None = None) -> None:
    """Invalidate the company fiscal-year cache. Called from a hook on
    Company-doc save so admins editing their fiscal year see the change
    on the next report run."""
    global _company_fy_start_month_cache
    if company:
        _company_fy_start_month_cache.pop(company, None)
    else:
        _company_fy_start_month_cache.clear()


def fiscal_year_bounds(company: str | None, fiscal_year: int | str, fy_start_month_override: int | None = None) -> Tuple[datetime.date, datetime.date]:
    """Return (start_date, end_date) for `company`'s `fiscal_year`.

    `fiscal_year` is the integer FY label as users refer to it:
      - KSA: "FY 2025" = Jan 1, 2025 → Dec 31, 2025.
      - India: "FY 2025" = Apr 1, 2024 → Mar 31, 2025 by Indian convention
        (the FY label is the calendar year the FY ENDS in). We adopt that
        convention here.

    v1.9.60 — when `fy_start_month_override` is provided (1-12), the
    override calendar is used instead of the company's configured start
    month. This enables group reporting: a Saudi subsidiary can render
    its KSA Jan-start books as Apr-Mar for parent reporting without
    altering Company.year_start_date.

    Algorithm:
      - Resolve the effective FY start month (override > company config > 1).
      - If m_start == 1: dates are Jan 1, FY → Dec 31, FY.
      - Else: dates are (FY - 1)-m_start-01 → FY-(m_start - 1)-last_day.
        e.g. India m_start=4: FY 2025 → 2024-04-01 to 2025-03-31.

    Defensive — if the company has no year_start_date AND no override,
    falls back to Jan-start, which is what the engine assumed pre-v1.9.59.
    """
    fy = cint(fiscal_year)
    m_start = get_company_fy_start_month(company, override=fy_start_month_override)
    if m_start == 1:
        start = datetime.date(fy, 1, 1)
        end = datetime.date(fy, 12, 31)
    else:
        # Start date is in the PRIOR calendar year.
        start = datetime.date(fy - 1, m_start, 1)
        # End date is the last day of the month before m_start in the
        # current calendar year.
        end_month = m_start - 1
        # Last day of end_month, FY year. Compute by stepping to the first
        # of the following month and subtracting one day — handles leap
        # year Feb correctly.
        if end_month == 12:
            next_month_first = datetime.date(fy + 1, 1, 1)
        else:
            next_month_first = datetime.date(fy, end_month + 1, 1)
        end = next_month_first - datetime.timedelta(days=1)
    return start, end


def calendar_month_for_fy_month(company: str | None, fy_month_idx: int, fy_start_month_override: int | None = None) -> int:
    """FY-month index (0..11) → calendar month (1..12).

    KSA (m_start=1): FY-month 0 → Jan (1), FY-month 11 → Dec (12).
    India (m_start=4): FY-month 0 → Apr (4), FY-month 11 → Mar (3).

    Wraps around the year boundary correctly. v1.9.60 — accepts an
    override calendar for group reporting (see get_company_fy_start_month).
    """
    m_start = get_company_fy_start_month(company, override=fy_start_month_override)
    return ((m_start - 1 + fy_month_idx) % 12) + 1


def fy_month_for_calendar_month(company: str | None, cal_month: int, fy_start_month_override: int | None = None) -> int:
    """Calendar month (1..12) → FY-month index (0..11). Inverse of above.

    Used when bucketing GL rows by their posting month into FY positions.
    v1.9.60 — accepts an override calendar for group reporting.
    """
    m_start = get_company_fy_start_month(company, override=fy_start_month_override)
    return (cal_month - m_start) % 12


def fy_month_range_to_date_range(
    company: str | None,
    fiscal_year: int | str,
    month_from: int,
    month_to: int,
    fy_start_month_override: int | None = None,
) -> Tuple[datetime.date, datetime.date]:
    """Convert a (FY-month-from, FY-month-to) slice within `fiscal_year`
    to absolute (start_date, end_date) bounds for SQL filtering.

    The two month indices are inclusive and in FY order (0..11).

    Used by every engine query path so the SQL WHERE clause is always
    `posting_date BETWEEN <start> AND <end>` rather than YEAR/MONTH
    extracts — which would break on non-Jan-start companies.

    v1.9.60 — accepts an override calendar for group reporting.
    """
    fy_start, fy_end = fiscal_year_bounds(company, fiscal_year, fy_start_month_override=fy_start_month_override)
    if month_from < 0:
        month_from = 0
    if month_to > 11:
        month_to = 11
    if month_to < month_from:
        month_to = month_from

    # Walk forward month_from months from fy_start.
    start = _add_months(fy_start, month_from)
    # End is the last day of the (fy_start + month_to)th month.
    end_month_first = _add_months(fy_start, month_to)
    # Last day of that month.
    end = _last_day_of_month(end_month_first)

    # Cap end at fy_end (defensive; should already be ≤ fy_end given inputs ≤ 11).
    if end > fy_end:
        end = fy_end
    return start, end


def _add_months(d: datetime.date, n: int) -> datetime.date:
    """Return the date n months after `d`, with day = 1. Used so partial
    months don't accumulate quirks (we never need a specific day, only
    the first-of-month for range starts)."""
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    return datetime.date(y, m + 1, 1)


def _last_day_of_month(d: datetime.date) -> datetime.date:
    """Last day of the month containing d. Handles leap-year Feb."""
    if d.month == 12:
        first_next = datetime.date(d.year + 1, 1, 1)
    else:
        first_next = datetime.date(d.year, d.month + 1, 1)
    return first_next - datetime.timedelta(days=1)


def format_fy_label(company: str | None, fiscal_year: int | str, fy_start_month_override: int | None = None) -> str:
    """Return the user-facing FY label for the company.

    KSA (Jan-start): "FY 2025"
    India (Apr-start): "FY 2024-25"
    Australia (Jul-start): "FY 2024-25"

    The convention: when fiscal year crosses a calendar year boundary,
    show both calendar years; otherwise just the single year. This matches
    how each region naturally writes their FY.

    v1.9.60 — when override is active, label reflects the override (e.g.
    a KSA company viewed as Apr-Mar shows "FY 2024-25" for group reporting).
    """
    fy = cint(fiscal_year)
    m_start = get_company_fy_start_month(company, override=fy_start_month_override)
    if m_start == 1:
        return f"FY {fy}"
    # Crosses the calendar boundary.
    return f"FY {fy - 1}-{str(fy)[-2:]}"


def clear_company_fy_cache_on_company_save(doc, method=None):
    """Hook handler for Company doc on_update events.

    When an admin changes a Company's year_start_date in ERPNext, the
    cached FY-start month for that company would otherwise stay stale
    until the worker process restarts. We clear just that company's
    entry to force re-read on the next report run.

    Defensive: never raises. A failed cache clear must not abort a
    Company save.
    """
    try:
        if doc and getattr(doc, "name", None):
            clear_company_fy_cache(doc.name)
    except Exception:
        pass


# ─── Date bounds resolver (v1.9.65) ──────────────────────────────────────


def resolve_date_bounds(
    company: str | None,
    fiscal_year: int | str,
    month_from: int,
    month_to: int,
    fy_start_month_override: int | None = None,
    period_mode: str = "fiscal_year",
    period_from_date: str | datetime.date | None = None,
    period_to_date: str | datetime.date | None = None,
) -> Tuple[datetime.date, datetime.date]:
    """Single source of truth for resolving an engine query's date bounds.

    v1.9.65 introduces `period_mode`. Two values:

      - 'fiscal_year' (default, preserves all v1.9.59-v1.9.64 behavior):
        Date bounds derived from `fiscal_year` + month range + any
        company FY start override. Behaves exactly like
        fy_month_range_to_date_range did before.

      - 'date_range': Date bounds taken directly from period_from_date
        and period_to_date. The fiscal year, month range, and start-
        month override are all IGNORED. This is the "show me what
        happened between these two dates regardless of FY" mode.

    The resolver returns (start_date, end_date) as datetime.date objects,
    suitable for direct use in SQL `BETWEEN` filters. Every engine path
    that needs to compute date bounds should go through this function
    rather than calling fy_month_range_to_date_range directly — that
    way the mode is honored consistently.

    Defensive — if mode='date_range' but the dates are missing or invalid,
    falls back to fiscal_year mode. This protects against frontend bugs
    that send mode=date_range without dates.
    """
    if period_mode == "date_range" and period_from_date and period_to_date:
        try:
            if isinstance(period_from_date, str):
                start = datetime.date.fromisoformat(period_from_date[:10])
            else:
                start = period_from_date
            if isinstance(period_to_date, str):
                end = datetime.date.fromisoformat(period_to_date[:10])
            else:
                end = period_to_date
            # Defensive: swap if user inverted them.
            if end < start:
                start, end = end, start
            return start, end
        except (ValueError, TypeError):
            # Bad dates — fall through to FY mode for safety.
            pass
    return fy_month_range_to_date_range(
        company, fiscal_year, month_from, month_to,
        fy_start_month_override=fy_start_month_override,
    )
