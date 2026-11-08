"""Expense Audit — company spending from bank statements (v3.7.0). Pure.

Every number is computed here from statement lines; nothing is estimated.
Line: {date, account, details, reference, type, credit, debit, currency}.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import unicodedata
from collections import defaultdict

MIDDLEMEN = (("apple.com", "Apple"), ("itunes", "Apple"), ("google", "Google"), ("paypal", "PayPal"),
             ("amzn", "Amazon"), ("amazon", "Amazon"), ("stripe", "Stripe"), ("paddle", "Paddle"),
             ("microsoft", "Microsoft"), ("msft", "Microsoft"))

# cost centre → keywords (lower-case, English and Arabic). First match wins.
CENTRES = (
    ("Internal transfers", ("my account in another bank", "account in other bank")),
    ("Payroll", ("payroll", "salary", "salaries", "رواتب", "wps")),
    ("Loans & financing", ("loan", "installment", "instalment", "managing debt", "تمويل", "قسط")),
    ("Government & visas", ("gosi", "التأمينات", "mol,", "وزارة العمل", "residents", "خدمات المقيمين", "istekdam",
                            "الاستقدام", "mofa", "الخارجية", "muqeem", "jawazat", "passport")),
    ("Taxes & Zakat", ("gatca", "zatca", "الزكاة", "vat payment")),
    ("Rent", ("ejar", "ايجار", "إيجار", "rent")),
    ("Utilities", ("saudi electricity", "السعودية للطاقة", "se,sub", "nwc", "water")),
    ("Telecom", ("stc", "mobily", "zain", "atheeb", "salam", "telecom", "الاتصالات")),
    ("Software & subscriptions", ("apple.com", "google", "microsoft", "oracle", "aws", "amazon web", "zoho", "adobe",
                                  "github", "openai", "anthropic", "atlassian", "slack", "notion", "frappe")),
    ("Advertising", ("advertising", "إعلان", "ads")),
    ("Professional services", ("law firm", "consult", "advisory", "audit", "محاماة")),
    ("Travel", ("airline", "saudia", "flynas", "flyadeal", "hotel", "booking.com", "uber", "careem")),
    ("Food & catering", ("catering", "تغذية", "restaurant", "coffee", "اعاشة")),
)

_NOISE = re.compile(r"\b(REF|VALUE DT|DATE-TIME|VIA (CORE SYSTEM|ECORP CHANNEL|MOBILE BANKING)|EXCHANGE RATE|CURRENCY|"
                    r"SAR|SA\d{22}|[A-Z]{4}SA\w*|SUB\d+|BILLER ID|ONE TIME BILL PAYMENT|ID#|TO|FROM|SENT DATE)\b", re.I)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def middleman(text: str) -> str | None:
    t = (text or "").lower()
    return next((name for key, name in MIDDLEMEN if key in t), None)


_SEG_SKIP = re.compile(r"^(REF\b|VIA\b|DATE|VALUE|EXCHANGE|CURRENCY|SENT DATE|THROUGH|ORG\.?AMOUNT|BILLER|SUB\d|"
                       r"ONE TIME|PERSONAL/OTHER|PURCHASE/EXPENSES|GOVERNMENT SERVICES|UTILITIES|SAUDI ARABIA|"
                       r"FILE\s*:|PAY\d|ID#|SAR\b|USD\b|SWF|LOCAL TRANSFER|INSTANT PAYMENT|FEE\b|VAT)", re.I)
_BIC = re.compile(r"^[A-Z]{4}SA[A-Z0-9]{2,5}$")
NAMED_TYPES = {"corporate payroll": "Corporate payroll", "bank fees/commissions": "Bank fees",
               "instant payment fees": "Instant payment fees"}


def clean_merchant(details: str, txn_type: str = "") -> str:
    """Best readable payee: names after TO:/FROM, the biller code, a clear
    transaction type, else the first meaningful comma-separated part of the
    bank text. 'SQ *JOES COFFEE 4421' → 'Joes Coffee'."""
    t = norm(details)
    # card-processor and middleman prefixes first: 'SQ *JOES COFFEE 4421' → 'JOES COFFEE 4421'
    t = re.sub(r"^(SQ|SP|TST|PAYPAL|GOOGLE|APPLE\.COM/BILL|AMZN MKTP|POS|MADA|VISA)\s*\*\s*", "", t, flags=re.I)
    if (txn_type or "").strip().lower() in NAMED_TYPES:
        return NAMED_TYPES[txn_type.strip().lower()]
    for pat in (r"\bTO\s*:\s*([^,]+)", r"\bFROM\s+([^,]+)", r"REMT INF:\s*([^,]+)"):
        m = re.search(pat, t, re.I)
        if m and re.search(r"[A-Za-z\u0600-\u06FF]{3}", m.group(1)):
            return _title(m.group(1))
    m = re.match(r"^([A-Z]{2,12}),SUB\d+", t)                      # government / utility billers: MOL, GOSI, EJAR…
    if m:
        return m.group(1).upper()
    for seg in (x.strip() for x in t.split(",")):
        if not seg or _SEG_SKIP.match(seg) or _BIC.match(seg) or re.match(r"^SA\d{10,}", seg):
            continue
        name = re.sub(r"\s+", " ", re.sub(r"\d+", " ", seg)).strip(" -/")
        if len(re.findall(r"[A-Za-z\u0600-\u06FF]", name)) < 4 or re.match(r"^(رقم|REF|TBC)\b", name):
            continue
        return _title(name)
    t = re.sub(r"^(SQ|SP|PAYPAL|GOOGLE|APPLE\.COM/BILL|AMZN MKTP|POS|MADA|VISA)\s*\*?\s*", "", t, flags=re.I)
    t = _NOISE.sub(" ", t)
    t = re.sub(r"[\d/:.,\-#*]+", " ", t)
    runs = [r.strip() for r in re.findall(r"[A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF &'.]{2,}", t)]
    runs = [r for r in runs if r.lower() not in ("local transfer", "instant payment", "payment", "personal other")]
    return _title(max(runs, key=len)) if runs else (txn_type or "Unknown").title()


def _title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" ,.-")[:60]
    return s.title() if s.isascii() else s


def _has_key(hay: str, k: str) -> bool:
    if k.isascii() and re.match(r"^[a-z]", k):          # whole words: 'rent' must not match 'current'
        tail = r"(?![a-z])" if k[-1].isalpha() else ""
        return re.search(r"(?<![a-z])" + re.escape(k) + tail, hay) is not None
    return k in hay


def centre(merchant: str, details: str, txn_type: str = "") -> str:
    tt = (txn_type or "").lower()
    if "fee" in tt or "commission" in tt or re.match(r"^\s*(local transfer|int\. transfer|payroll processing),\s*fee", (details or "").lower()):
        return "Bank fees"                               # by the bank's own transaction type, not the description
    hay = f"{merchant} {details} {txn_type}".lower()
    for name, keys in CENTRES:
        if any(_has_key(hay, k) for k in keys):
            return name
    return "Other"


def pair_internal(lines: list[dict], days: int = 3) -> set[int]:
    """Indices of same-amount money moving out of one company account and into
    another within `days` — transfers between own accounts."""
    outs = [(i, ln) for i, ln in enumerate(lines) if ln["debit"] > 0]
    ins = [(i, ln) for i, ln in enumerate(lines) if ln["credit"] > 0]
    used, pairs = set(), set()
    for i, o in outs:
        for j, n in ins:
            if j in used or n["account"] == o["account"]:
                continue
            if abs(n["credit"] - o["debit"]) < 0.005 and abs((n["date"] - o["date"]).days) <= days:
                used.add(j)
                pairs.update({i, j})
                break
    return pairs


def classify(lines: list[dict], own_names: tuple[str, ...] = ()) -> list[dict]:
    """Adds merchant, via, centre and kind (spend | income | internal | refund)."""
    paired = pair_internal(lines)
    # own-company names: 4+ characters, whole words ('IR' must not match 'SIRION')
    own = tuple(n.lower() for n in own_names if n and len(n.strip()) >= 4)
    out = []
    for i, ln in enumerate(lines):
        d = norm(ln.get("details", ""))
        via = middleman(d)
        merchant = clean_merchant(d, ln.get("type", ""))
        c = centre(merchant, d, ln.get("type", ""))
        low = d.lower()
        if i in paired or c == "Internal transfers" or any(_has_key(low, o) for o in own):
            kind = "internal"
        elif ln["credit"] > 0 and re.search(r"refund|reversal|استرداد|مرتجع", low):
            kind = "refund"
        else:
            kind = "spend" if ln["debit"] > 0 else "income"
        out.append({**ln, "merchant": merchant, "via": via, "centre": c if kind != "internal" else "Internal transfers",
                    "kind": kind, "amount": ln["debit"] or ln["credit"]})
    return out


def monthly(rows: list[dict]) -> list[dict]:
    m: dict[str, dict] = defaultdict(lambda: {"in": 0.0, "out": 0.0})
    for r in rows:
        if r["kind"] == "internal":
            continue
        k = r["date"].strftime("%Y-%m")
        if r["kind"] in ("income", "refund"):
            m[k]["in"] += r["credit"]
        else:
            m[k]["out"] += r["debit"]
    return [{"month": k, "in": round(v["in"], 2), "out": round(v["out"], 2), "left": round(v["in"] - v["out"], 2)}
            for k, v in sorted(m.items())]


def centres(rows: list[dict]) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "count": 0, "merchants": defaultdict(float)})
    for r in rows:
        if r["kind"] != "spend":
            continue
        a = agg[r["centre"]]
        a["total"] += r["debit"]
        a["count"] += 1
        a["merchants"][r["merchant"]] += r["debit"]
    out = []
    for name, a in agg.items():
        top = sorted(a["merchants"].items(), key=lambda kv: -kv[1])[:8]
        out.append({"centre": name, "total": round(a["total"], 2), "count": a["count"],
                    "merchants": [{"merchant": k, "total": round(v, 2)} for k, v in top]})
    return sorted(out, key=lambda x: -x["total"])


def recurring(rows: list[dict], tol: float = 0.05) -> list[dict]:
    """Charges that repeat: same payee (or middleman + amount) at a regular
    interval. Weekly 5–9 days, monthly 26–35, quarterly 85–95, yearly 350–380."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["kind"] != "spend":
            continue
        key = (r["via"], round(r["debit"], 0)) if r["via"] else (r["merchant"],)
        groups[key].append(r)
    found = []
    for key, g in groups.items():
        if len(g) < 2:
            continue
        g.sort(key=lambda r: r["date"])
        amounts = [r["debit"] for r in g]
        base = statistics.median(amounts)
        if base <= 0 or any(abs(a - base) / base > max(tol, 0.25 if len(key) == 1 else tol) for a in amounts):
            continue
        gaps = [(b["date"] - a["date"]).days for a, b in zip(g, g[1:], strict=False)]
        gap = statistics.median(gaps)
        period = ("Weekly" if 5 <= gap <= 9 else "Monthly" if 26 <= gap <= 35 else
                  "Quarterly" if 85 <= gap <= 95 else "Yearly" if 350 <= gap <= 380 else None)
        if not period:
            continue
        step = {"Weekly": 7, "Monthly": 30, "Quarterly": 91, "Yearly": 365}[period]
        per_year = {"Weekly": 52, "Monthly": 12, "Quarterly": 4, "Yearly": 1}[period]
        last = g[-1]
        found.append({"merchant": g[-1]["merchant"], "via": key[0] if len(key) == 2 else None, "period": period,
                      "price": round(last["debit"], 2), "per_year": round(last["debit"] * per_year, 2),
                      "count": len(g), "last": str(last["date"]), "next": str(last["date"] + dt.timedelta(days=step)),
                      "price_rise": round(last["debit"] - g[0]["debit"], 2) if last["debit"] > g[0]["debit"] * 1.01 else 0.0,
                      "centre": last["centre"]})
    return sorted(found, key=lambda x: -x["per_year"])
