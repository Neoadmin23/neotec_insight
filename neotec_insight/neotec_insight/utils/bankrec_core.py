"""Bank reconciliation — reading statements and matching (v3.6.0). Pure.

Reading: words with page positions (from the PDF), a bank *format* saying
which header label starts each column, how dates are written, and whether
the statement lists newest or oldest first. A row is anchored on its date
(banks print it on the row's middle line); every other word belongs to the
nearest anchor above or below on that page.

Proof: each line's balance must equal the previous balance + credit − debit,
walking in posting order. A line that breaks the chain is flagged before
anything is posted.

Matching (rules; AI handles the rest, validated by the same rules):
  1 reference — a voucher's reference number appears in the bank text and
    amount + direction agree;
  2 amount + date — one candidate with the same amount and direction within
    the date window, preferring one whose party is named in the text;
  3 combination — two or three entries for one party summing to the line.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import re
import unicodedata

COLS = ("date", "details", "reference", "cheque", "type", "credit", "debit", "balance")


def pdf_words(content: bytes) -> list[list[dict]]:
    """Words with positions per page, via PyMuPDF (already an Insight dependency)."""
    try:
        import pymupdf as fitz
    except ImportError:            # older PyMuPDF
        import fitz
    out = []
    with fitz.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            out.append([{"text": w[4], "x0": w[0], "x1": w[2], "top": w[1]} for w in page.get_text("words")])
    return out


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def to_amount(s) -> float | None:
    t = re.sub(r"[^\d.\-]", "", str(s or "").replace(",", ""))
    if not t or t in ("-", ".", "-."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s: str, fmt: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s.strip(), fmt).date()
    except Exception:
        return None


def column_starts(words: list[dict], fmt: dict) -> dict[str, float] | None:
    """Find the header row on a page: x0 of each column label."""
    labels = {c: v for c, v in fmt["columns"].items() if v}   # col -> header label ("Word" or "Word#n")
    first = _label_word(labels["date"])
    # The same words can appear elsewhere on the page (a "Transaction Type"
    # field in a details box): take the labels that share the date label's line.
    for anchor in (w for w in words if w["text"] == first):
        row = sorted((w for w in words if abs(w["top"] - anchor["top"]) < 3), key=lambda x: x["x0"])
        hits: dict[str, dict] = {}
        for col, label in labels.items():
            word, nth = _label_word(label), _label_nth(label)
            same = [x for x in row if x["text"] == word]
            if len(same) >= nth:
                hits[col] = same[nth - 1]
        if len(hits) >= 5:
            return {c: h["x0"] for c, h in hits.items()} | {"_header_top": anchor["top"]}
    return None


def _label_word(label: str) -> str:
    """'Transaction#2' → 'Transaction'; 'Value Date' → 'Value'."""
    return str(label).split("#")[0].split()[0]


def _label_nth(label: str) -> int:
    """Which occurrence on the header line: 'Transaction#2' → 2 (default 1)."""
    m = re.search(r"#(\d+)$", str(label))
    return int(m.group(1)) if m else 1


def _col_of(x: float, starts: dict[str, float], slack: float) -> str | None:
    order = sorted(((v, c) for c, v in starts.items() if not c.startswith("_")))
    col = None
    for v, c in order:
        if x >= v - slack:
            col = c
    return col


def parse_pages(pages: list[list[dict]], fmt: dict) -> list[dict]:
    """Words per page → statement lines in printed order."""
    slack = float(fmt.get("slack", 10))
    date_fmt = fmt.get("date_format", "%d/%m/%Y")
    lines = []
    starts = None
    for pno, words in enumerate(pages, 1):
        starts = column_starts(words, fmt) or starts
        if not starts:
            continue
        head = starts.get("_header_top", 0)
        body = [w for w in words if w["top"] > head + 4 and not _is_footer(w, fmt)]
        anchors = []
        for w in body:
            if _col_of(w["x0"], starts, slack) == "date":
                d = parse_date(w["text"], date_fmt)
                if d:
                    anchors.append({"top": w["top"], "date": d, "words": {c: [] for c in COLS}})
        if not anchors:
            continue
        anchors.sort(key=lambda a: a["top"])
        for w in body:
            col = _col_of(w["x0"], starts, slack)
            if not col or col == "date":
                continue
            a = min(anchors, key=lambda a: abs(a["top"] - w["top"]))
            a["words"][col].append(w)
        for a in anchors:
            def txt(c, a=a):
                return norm_text(" ".join(x["text"] for x in sorted(a["words"][c], key=lambda x: (round(x["top"]), x["x0"]))))
            same_line = lambda c, a=a: [x for x in a["words"][c] if abs(x["top"] - a["top"]) < 3]   # noqa: E731
            amt = lambda c, sl=same_line: to_amount(" ".join(x["text"] for x in sl(c)))             # noqa: E731
            lines.append({"page": pno, "date": a["date"], "details": txt("details"), "reference": txt("reference"),
                          "cheque": txt("cheque"), "type": txt("type"),
                          "credit": amt("credit") or 0.0, "debit": amt("debit") or 0.0, "balance": amt("balance")})
    return lines


def _is_footer(w: dict, fmt: dict) -> bool:
    return any(w["text"].startswith(m) for m in fmt.get("footer_markers", []))


def posting_order(lines: list[dict], fmt: dict) -> list[dict]:
    return list(reversed(lines)) if fmt.get("order") == "newest_first" else list(lines)


def prove(lines_in_posting_order: list[dict]) -> dict:
    """Running-balance proof. The first line's own balance fixes the opening."""
    out, bad = [], []
    prev = None
    for i, ln in enumerate(lines_in_posting_order):
        if ln.get("balance") is None:
            ok = False
        elif prev is None:
            ok = True
        else:
            ok = abs(prev + ln["credit"] - ln["debit"] - ln["balance"]) < 0.005
        if not ok:
            bad.append(i)
        out.append({**ln, "balance_ok": ok})
        prev = ln.get("balance") if ln.get("balance") is not None else prev
    first = lines_in_posting_order[0] if lines_in_posting_order else None
    opening = round(first["balance"] - first["credit"] + first["debit"], 2) if first and first.get("balance") is not None else None
    return {"lines": out, "ok": not bad, "bad": bad, "opening": opening,
            "closing": lines_in_posting_order[-1]["balance"] if lines_in_posting_order else None,
            "credits": round(sum(x["credit"] for x in lines_in_posting_order), 2),
            "debits": round(sum(x["debit"] for x in lines_in_posting_order), 2)}


def line_key(account: str, ln: dict) -> str:
    """Stable per-line id: identical lines on one day differ by their balance."""
    raw = f"{account}|{ln['date']}|{ln.get('reference','')}|{ln['credit']:.2f}|{ln['debit']:.2f}|{ln.get('balance')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


# ── matching ─────────────────────────────────────────────────────────────────

def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-z]+", norm_text(s).lower()) if len(t) >= 3}


def party_named(party: str, text: str) -> bool:
    pt = _tokens(party) - {"company", "co", "ltd", "llc", "est", "the", "for", "and", "trading"}
    return bool(pt) and len(pt & _tokens(text)) >= min(2, len(pt))


def match(lines: list[dict], cands: list[dict], days: int = 3, ref_days: int = 30) -> list[dict]:
    """lines: {key, date, details, reference, credit, debit}; cands: {doctype,
    name, date, amount, direction ('in'|'out'), reference, party}. Each candidate
    is used once. Returns [{key, doctype, name(s), confidence, rule}]."""
    used, out = set(), []

    def amt(ln):
        return round(ln["credit"] or ln["debit"], 2), ("in" if ln["credit"] else "out")

    def near(ln, c, n):
        return abs((c["date"] - ln["date"]).days) <= n

    open_lines = []
    for ln in lines:                                   # pass 1: reference
        a, dirn = amt(ln)
        text = f"{ln.get('details','')} {ln.get('reference','')}".lower()
        hit = next((c for c in cands if c["name"] not in used and c["direction"] == dirn
                    and round(c["amount"], 2) == a and near(ln, c, ref_days)
                    and c.get("reference") and len(c["reference"]) >= 5 and c["reference"].lower() in text), None)
        if hit:
            used.add(hit["name"])
            out.append({"key": ln["key"], "vouchers": [(hit["doctype"], hit["name"], a)], "confidence": 0.99, "rule": "reference"})
        else:
            open_lines.append(ln)
    rest = []
    for ln in open_lines:                              # pass 2: amount + date (+ party)
        a, dirn = amt(ln)
        pool = [c for c in cands if c["name"] not in used and c["direction"] == dirn
                and round(c["amount"], 2) == a and near(ln, c, days)]
        named = [c for c in pool if c.get("party") and party_named(c["party"], ln.get("details", ""))]
        pick = named[0] if len(named) == 1 else (pool[0] if len(pool) == 1 and not pool[0].get("party") else None)
        if pick:
            used.add(pick["name"])
            out.append({"key": ln["key"], "vouchers": [(pick["doctype"], pick["name"], a)],
                        "confidence": 0.95 if named else 0.85, "rule": "amount+party" if named else "amount+date"})
        else:
            rest.append(ln)
    for ln in rest:                                    # pass 3: 2–3 entries for one named party
        a, dirn = amt(ln)
        pool = [c for c in cands if c["name"] not in used and c["direction"] == dirn and near(ln, c, days)
                and c.get("party") and party_named(c["party"], ln.get("details", ""))][:12]
        found = None
        for n in (2, 3):
            for combo in itertools.combinations(pool, n):
                if len({c["party"] for c in combo}) == 1 and abs(sum(c["amount"] for c in combo) - a) < 0.005:
                    found = combo
                    break
            if found:
                break
        if found:
            used.update(c["name"] for c in found)
            out.append({"key": ln["key"], "vouchers": [(c["doctype"], c["name"], round(c["amount"], 2)) for c in found],
                        "confidence": 0.9, "rule": "combination"})
    return out


def validate_ai(line: dict, vouchers: list[tuple], cands: dict[str, dict], used: set[str], days: int = 7) -> str | None:
    """None if an AI-proposed match is acceptable, else the reason."""
    a = round(line["credit"] or line["debit"], 2)
    dirn = "in" if line["credit"] else "out"
    total = 0.0
    for _dt, name, _amt in vouchers:
        c = cands.get(name)
        if not c:
            return f"unknown entry {name}"
        if name in used:
            return f"{name} already matched"
        if c["direction"] != dirn:
            return f"{name} goes the other way"
        if abs((c["date"] - line["date"]).days) > days:
            return f"{name} is more than {days} days away"
        total += c["amount"]
    if abs(total - a) >= 0.005:
        return f"amounts add up to {total:,.2f}, not {a:,.2f}"
    return None


# ── format mapper (v3.6.2) ────────────────────────────────────────────────────

KEYWORDS = {
    "date": ("date", "value", "posting", "txn", "تاريخ", "التاريخ"),
    "details": ("details", "description", "narration", "particulars", "remarks", "البيان", "التفاصيل", "الوصف"),
    "reference": ("reference", "ref", "ref.", "المرجع", "مرجع"),
    "cheque": ("cheque", "check", "chq", "شيك", "الشيك"),
    "type": ("type", "transaction", "النوع", "نوع"),
    "credit": ("credit", "deposit", "deposits", "cr", "دائن", "إيداع", "ايداع"),
    "debit": ("debit", "withdrawal", "withdrawals", "dr", "مدين", "سحب"),
    "balance": ("balance", "الرصيد", "رصيد"),
}


def header_candidates(pages: list[list[dict]], limit: int = 6) -> list[dict]:
    """Lines that look like a column header: several words, at least three of
    them known column keywords. Best first."""
    out = []
    for pno, words in enumerate(pages[:3], 1):
        rows: dict[int, list[dict]] = {}
        for w in words:
            rows.setdefault(round(w["top"] / 2.5), []).append(w)
        for _k, row in rows.items():
            row = sorted(row, key=lambda x: x["x0"])
            hits = sum(1 for w in row if any(w["text"].lower().strip(".:") in kw for kw in KEYWORDS.values()))
            if len(row) >= 4 and hits >= 3:
                out.append({"page": pno, "top": round(row[0]["top"], 1), "score": hits,
                            "words": [{"text": w["text"], "x0": round(w["x0"], 1), "x1": round(w.get("x1", w["x0"]), 1)}
                                      for w in row]})
    seen, best = set(), []
    for c in sorted(out, key=lambda c: (-c["score"], c["page"], c["top"])):
        sig = " ".join(w["text"] for w in c["words"])
        if sig not in seen:
            seen.add(sig)
            best.append(c)
    return best[:limit]


def word_labels(words: list[dict]) -> list[str]:
    """Selectable labels for a header line: 'Word', or 'Word#n' for repeats."""
    count: dict[str, int] = {}
    out = []
    for w in words:
        count[w["text"]] = count.get(w["text"], 0) + 1
        out.append(w["text"] if count[w["text"]] == 1 else f"{w['text']}#{count[w['text']]}")
    return out


def phrases(words: list[dict], gap: float = 6.0) -> list[list[int]]:
    """Group header words into phrases ("Transaction Date") by the gap between
    one word's right edge and the next word's left edge. Indices into words."""
    out: list[list[int]] = []
    for i, w in enumerate(words):
        if out and "x1" in words[i - 1] and w["x0"] - words[i - 1]["x1"] <= gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


PRIORITY = ("balance", "credit", "debit", "cheque", "reference", "details", "date", "type")


def suggest_mapping(words: list[dict]) -> dict[str, str]:
    """Guess which header phrase is each column (English or Arabic) and return
    the phrase's first word as the label ('Word' or 'Word#n' when repeated)."""
    labels = word_labels(words)
    taken, out = set(), {}
    for ph in phrases(words):
        texts = [words[i]["text"].lower().strip(".:") for i in ph]
        for col in PRIORITY:
            if col not in out and any(t in KEYWORDS[col] for t in texts):
                out[col] = labels[ph[0]]
                taken.add(col)
                break
    return out


DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d")


def guess_date_format(pages: list[list[dict]]) -> tuple[str | None, int]:
    """The format that parses the most date-like words (ties: day-first)."""
    tokens = [w["text"] for p in pages[:3] for w in p if re.match(r"^\d{1,4}[-/.][\dA-Za-z]{1,3}[-/.]\d{2,4}$", w["text"])]
    best, n = None, 0
    for f in DATE_FORMATS:
        k = sum(1 for t in tokens if parse_date(t, f))
        if k > n:
            best, n = f, k
    return best, n


def try_orders(pages, fmt: dict) -> tuple[str, dict, list[dict]]:
    """With order 'auto', pick the order whose balances prove (or prove best)."""
    orders = ("newest_first", "oldest_first") if fmt.get("order") in (None, "", "auto") else (fmt["order"],)
    best = None
    for o in orders:
        f = {**fmt, "order": o}
        lines = parse_pages(pages, f)
        proof = prove(posting_order(lines, f)) if lines else {"ok": False, "bad": [], "lines": []}
        score = (bool(lines) and proof["ok"], -len(proof["bad"]), len(lines))
        if best is None or score > best[0]:
            best = (score, o, proof, lines)
    return best[1], best[2], best[3]
