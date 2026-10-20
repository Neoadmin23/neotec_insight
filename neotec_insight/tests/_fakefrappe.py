"""A small in-memory stand-in for the parts of frappe the backup, snapshot and
allocation-save paths use, with real commit/rollback semantics. It lets those
paths run end to end in CI without a site. Doctype metadata comes from the
app's own doctype JSON files."""
from __future__ import annotations

import copy
import datetime
import json
import sys
import types
import uuid
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "neotec_insight"


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


def _load_meta():
    metas = {}
    for folder in (APP / "doctype").iterdir():
        j = folder / f"{folder.name}.json"
        if j.exists():
            d = json.loads(j.read_text())
            metas[d["name"]] = d
    return metas


class _DF(dict):
    __getattr__ = dict.get


class _Meta:
    def __init__(self, d):
        self._d = d
        self.fields = [_DF(f) for f in d["fields"]]
        self.istable = d.get("istable")

    def get_field(self, fn):
        return next((f for f in self.fields if f["fieldname"] == fn), None)

    def get_label(self, fn):
        f = self.get_field(fn)
        return (f or {}).get("label") or fn

    @property
    def is_submittable(self):
        return bool(self._d.get("is_submittable"))

    @property
    def issingle(self):
        return bool(self._d.get("issingle"))


class _dict(dict):
    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v


class Flags(dict):
    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v


class FakeSite:
    def __init__(self):
        self.metas = _load_meta()
        self.tables: dict[str, dict] = {}       # doctype -> name -> record (with child lists)
        self.committed = {}
        self.deleted_log: list[dict] = []
        self.errors: list[str] = []
        self.counter = 0
        self.queries = 0
        self.savepoints: dict = {}
        self.user = "Administrator"
        self.roles: dict[str, set] = {}          # user -> roles
        self.perm = None                         # callable(doctype, ptype, doc, user) -> bool | None
        self.allowed_companies: dict[str, list] = {}   # user -> companies (User Permissions)
        self.redis: dict = {}                    # shared cache: (site, key) -> value
        self.site_name = "test.site"
        self.commit()

    def commit(self):
        self.committed = copy.deepcopy(self.tables)

    def rollback(self, save_point=None):
        if save_point:
            self.tables = copy.deepcopy(self.savepoints[save_point])
        else:
            self.tables = copy.deepcopy(self.committed)

    def table(self, dt):
        return self.tables.setdefault(dt, {})


SITE: FakeSite | None = None


def _match(rec, filters):
    if not filters:
        return True
    items = filters.items() if isinstance(filters, dict) else [
        ((f[1], f[2:]) if len(f) == 4 else (f[0], f[1:])) for f in filters]
    for k, cond in items:
        v = rec.get(k)
        if isinstance(cond, (list, tuple)) and cond and isinstance(cond[0], str) and cond[0] in (
                "between", "in", "not in", "is", "like", "!=", "=", ">", "<", ">=", "<="):
            op, arg = cond[0], cond[1]
            sv = str(v) if v is not None else None
            if op == "between":
                if sv is None or not (str(arg[0]) <= sv[:10] <= str(arg[1])):
                    return False
            elif op == "in":
                if v not in arg:
                    return False
            elif op == "not in":
                if v in arg:
                    return False
            elif op == "is":
                isset = v not in (None, "")
                if (arg == "set") != isset:
                    return False
            elif op == "like":
                pat = arg.replace("%", "")
                if pat not in str(v or ""):
                    return False
            elif op == "!=":
                if v == arg:
                    return False
            elif op == "=":
                if v != arg:
                    return False
        else:
            if (str(v) if isinstance(v, (datetime.date,)) else v) != cond:
                return False
    return True


class Document:
    def __init__(self, doctype, data=None):
        object.__setattr__(self, "doctype", doctype)
        object.__setattr__(self, "flags", Flags())
        object.__setattr__(self, "_data", {})
        meta = SITE.metas.get(doctype, {"fields": []})
        for f in meta["fields"]:
            if f.get("default") is not None and f["fieldtype"] not in ("Table", "Table MultiSelect"):
                dv = f["default"]
                if f["fieldtype"] in ("Check", "Int"):
                    dv = int(dv)
                elif f["fieldtype"] in ("Float", "Currency"):
                    dv = float(dv)
                self._data[f["fieldname"]] = dv
            if f["fieldtype"] in ("Table", "Table MultiSelect"):
                self._data[f["fieldname"]] = []
        if data:
            self.update(data)

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        return self._data.get(k)

    def __setattr__(self, k, v):
        self._data[k] = v

    def get(self, k, default=None):
        return self._data.get(k, default)

    def update(self, d):
        for k, v in d.items():
            if k == "doctype":
                continue
            if isinstance(v, list):
                v = [(_dict(x.as_dict()) if isinstance(x, Document) else _dict(x)) for x in v]
            self._data[k] = v
        return self

    def as_dict(self):
        out = dict(self._data)
        out["doctype"] = self.doctype
        out.setdefault("owner", "Administrator")
        out.setdefault("creation", "2026-10-13 00:00:00")
        out.setdefault("modified", "2026-10-13 00:00:00")
        for k, v in list(out.items()):
            if isinstance(v, list):
                out[k] = [dict(x, parent=self.name, parenttype=self.doctype, parentfield=k, idx=i + 1)
                          for i, x in enumerate(v)]
        return out

    def _validate(self):
        if self.doctype == "Insight Allocation Entry":
            for r in SITE.table(self.doctype).values():
                if (r["name"] != self.name and r.get("rule") == self.rule
                        and r.get("cost_center") == self.cost_center
                        and str(r.get("period_month")) == str(self.period_month)):
                    raise ValidationError("duplicate allocation entry")
        meta = SITE.metas.get(self.doctype, {"fields": []})
        for f in meta["fields"]:
            if f.get("reqd") and self._data.get(f["fieldname"]) in (None, ""):
                raise ValidationError(f"{f['fieldname']} is mandatory")
        if not self.flags.ignore_links:
            for f in meta["fields"]:
                if f["fieldtype"] == "Link" and f["options"] in SITE.metas and self._data.get(f["fieldname"]):
                    if self._data[f["fieldname"]] not in SITE.table(f["options"]):
                        raise ValidationError(f"Could not find {f['options']}: {self._data[f['fieldname']]}")

    def insert(self, ignore_permissions=False, set_name=None):
        meta = SITE.metas.get(self.doctype, {})
        if set_name:
            self._data["name"] = set_name
        elif not self._data.get("name"):
            an = meta.get("autoname") or "hash"
            if an.startswith("field:"):
                self._data["name"] = self._data.get(an.split(":", 1)[1])
            else:
                SITE.counter += 1
                self._data["name"] = (f"SNAP-{SITE.counter:05d}" if self.doctype == "Insight Backup Snapshot"
                                      else uuid.uuid4().hex[:10])
        if self.name in SITE.table(self.doctype):
            raise ValidationError(f"Duplicate name {self.name}")
        self._validate()
        rec = self.as_dict()
        SITE.counter += 1
        rec["creation"] = f"2026-10-13 00:{SITE.counter // 60 % 60:02d}:{SITE.counter % 60:02d}.{SITE.counter:06d}"
        SITE.table(self.doctype)[self.name] = rec
        return self

    def save(self, ignore_permissions=False):
        if not self._data.get("name") or self.name not in SITE.table(self.doctype):
            return self.insert()
        self._validate()
        rec = self.as_dict()
        rec["creation"] = SITE.table(self.doctype)[self.name].get("creation")
        SITE.table(self.doctype)[self.name] = rec
        return self

    def db_set(self, d):
        self.update(d)
        self.save()


def _rec_to_doc(dt, rec):
    d = Document(dt)
    d._data.update({k: ([_dict(x) for x in copy.deepcopy(v)] if isinstance(v, list) else copy.deepcopy(v))
                    for k, v in rec.items() if k != "doctype"})
    return d


def get_doc(arg, name=None):
    if not isinstance(arg, dict):
        SITE.queries += 1
    if isinstance(arg, dict):
        return Document(arg["doctype"], arg)
    meta = SITE.metas.get(arg, {})
    if name is None and meta.get("issingle"):
        rec = SITE.table(arg).get(arg) or {"name": arg}
        return _rec_to_doc(arg, rec)
    rec = SITE.table(arg).get(name)
    if rec is None:
        raise ValidationError(f"{arg} {name} not found")
    return _rec_to_doc(arg, rec)


def new_doc(dt):
    return Document(dt)


def _grouped(rows, fields, group_by):
    groups: dict = {}
    for r in rows:
        groups.setdefault(r.get(group_by), []).append(r)
    out = []
    for key, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        rec = _dict()
        for f in fields:
            alias = f.split(" as ")[-1].strip()
            expr = f.split(" as ")[0].strip()
            if expr.startswith("count("):
                rec[alias] = len(items)
            elif expr.startswith("min("):
                rec[alias] = min(str(x.get(expr[4:-1])) for x in items)
            elif expr.startswith("max("):
                rec[alias] = max(str(x.get(expr[4:-1])) for x in items)
            else:
                rec[alias] = items[0].get(expr)
        out.append(rec)
    return out


def get_all(dt, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None,
            or_filters=None, group_by=None, **_):
    SITE.queries += 1
    if SITE.metas.get(dt, {}).get("istable"):
        rows = []
        for table in SITE.tables.values():
            for rec in table.values():
                for v in rec.values():
                    if isinstance(v, list):
                        rows.extend(dict(x) for x in v if isinstance(x, dict) and _match(x, filters))
        rows.sort(key=lambda r: r.get("idx") or 0)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [_dict(r) if fields == ["*"] else _dict({f: r.get(f) for f in fields or ["name"]})
                for r in rows]
    if dt == "DocType":
        rows = [{"name": n, "issingle": m.get("issingle", 0), "istable": m.get("istable", 0),
                 "module": m.get("module")} for n, m in SITE.metas.items()]
        rows = [r for r in rows if _match(r, filters)]
    else:
        rows = [r for r in SITE.table(dt).values() if _match(r, filters)]
    if group_by:
        return _grouped(rows, fields or ["name"], group_by)
    if order_by and "creation" in order_by:
        rows.sort(key=lambda r: str(r.get("creation")), reverse="desc" in order_by)
    if limit_page_length:
        rows = rows[:int(limit_page_length)]
    if pluck:
        return [r.get(pluck) for r in rows]
    fields = fields or ["name"]
    if fields == ["*"]:
        return [_dict(copy.deepcopy({k: v for k, v in r.items() if not isinstance(v, list)}))
                for r in rows]
    return [_dict({f: r.get(f) for f in fields}) for r in rows]


def delete_doc(dt, name, force=0, ignore_permissions=False, ignore_on_trash=False,
               delete_permanently=False, **_):
    rec = SITE.table(dt).pop(name, None)
    if rec is None:
        raise ValidationError(f"{dt} {name} not found")
    if not delete_permanently:
        SITE.deleted_log.append({"deleted_doctype": dt, "deleted_name": name, "data": rec})
        nm = uuid.uuid4().hex[:10]
        SITE.table("Deleted Document")[nm] = {"name": nm, "deleted_doctype": dt, "deleted_name": name,
                                              "restored": 0, "data": json.dumps(rec, default=str),
                                              "owner": "Administrator", "creation": "2026-10-13"}


class _DB:
    def exists(self, dt, name_or_filters=None):
        if dt == "DocType":
            return name_or_filters in SITE.metas
        if isinstance(name_or_filters, dict):
            hit = [r for r in SITE.table(dt).values() if _match(r, name_or_filters)]
            return hit[0]["name"] if hit else None
        meta = SITE.metas.get(dt, {})
        if meta.get("issingle"):
            return dt in SITE.table(dt) and dt or None
        return name_or_filters if name_or_filters in SITE.table(dt) else None

    def get_value(self, dt, filters, fieldname="name", as_dict=False):
        if isinstance(filters, str):
            rec = SITE.table(dt).get(filters)
        else:
            hit = [r for r in SITE.table(dt).values() if _match(r, filters)]
            rec = hit[0] if hit else None
        if rec is None:
            return None
        if isinstance(fieldname, (list, tuple)):
            d = _dict({f: rec.get(f) for f in fieldname})
            return d if as_dict else tuple(d.values())
        return rec.get(fieldname)

    def set_value(self, dt, name, field, value=None, update_modified=True):
        rec = SITE.table(dt)[name]
        if isinstance(field, dict):
            rec.update(field)
        else:
            rec[field] = value

    def count(self, dt, filters=None):
        return len([r for r in SITE.table(dt).values() if _match(r, filters)])

    def table_exists(self, dt):
        return dt in SITE.metas

    def commit(self):
        SITE.commit()

    def rollback(self, save_point=None):
        SITE.rollback(save_point)

    def savepoint(self, name):
        SITE.savepoints[name] = copy.deepcopy(SITE.tables)

    def sql(self, *a, **k):
        return []


def install():
    """Put a fresh fake site and module into sys.modules['frappe']."""
    global SITE
    SITE = FakeSite()
    m = types.ModuleType("frappe")
    m.ValidationError = ValidationError
    m.PermissionError = PermissionError_
    m._dict = _dict
    m.flags = Flags()
    m.local = types.SimpleNamespace(site="test.site")
    m.db = _DB()
    m.get_doc, m.new_doc, m.get_all, m.delete_doc = get_doc, new_doc, get_all, delete_doc
    m.get_meta = lambda dt: _Meta(SITE.metas[dt])
    m.get_value = m.db.get_value
    def has_permission(doctype, ptype="read", doc=None, user=None, **k):
        if SITE.perm is not None:
            r = SITE.perm(doctype, ptype, doc, user or SITE.user)
            if r is not None:
                return r
        return True
    m.has_permission = has_permission

    def get_roles(user=None):
        u = user or SITE.user
        if u == "Administrator":
            return ["Administrator", "System Manager"]
        return sorted(SITE.roles.get(u, {"System Manager"}))
    m.get_roles = get_roles

    def get_list(dt, *a, **k):
        rows = get_all(dt, *a, **k)
        allowed = SITE.allowed_companies.get(SITE.user)
        if dt == "Company" and allowed is not None:
            if k.get("pluck"):
                return [r for r in rows if r in allowed]
            return [r for r in rows if r.get("name") in allowed]
        if SITE.perm is not None and not k.get("group_by"):
            # Like Frappe: permissions apply to the record, whichever fields
            # are selected — so check on full rows, then project.
            full = get_all(dt, *a, **{**k, "fields": ["*"], "pluck": None})
            keep = [r for r in full if SITE.perm(dt, "read", r.get("name"), SITE.user) is not False]
            if k.get("pluck"):
                return [r.get(k["pluck"]) for r in keep]
            fields = k.get("fields") or ["name"]
            if fields == ["*"]:
                return keep
            return [_dict({f: r.get(f) for f in fields}) for r in keep]
        return rows
    m.get_list = get_list
    m.get_single = lambda dt: get_doc(dt)

    class _Session:
        @property
        def user(self):
            return SITE.user
    m.session = _Session()

    def set_user(u):
        SITE.user = u
    m.set_user = set_user
    m.clear_messages = lambda: None
    m.conf = _dict()
    m.request = types.SimpleNamespace(path="/")
    m.get_site_path = lambda *parts: "/".join(["/tmp/fakesite"] + list(parts))
    m.DoesNotExistError = type("DoesNotExistError", (ValidationError,), {})

    class _Cache:
        def get_value(self, key):
            return copy.deepcopy(SITE.redis.get((SITE.site_name, key)))

        def set_value(self, key, val, expires_in_sec=None):
            SITE.redis[(SITE.site_name, key)] = copy.deepcopy(val)

        def delete_value(self, key):
            SITE.redis.pop((SITE.site_name, key), None)
    m.cache = _Cache()
    m.only_for = lambda *a, **k: None
    m.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    m.parse_json = lambda s: json.loads(s) if isinstance(s, str) else s
    m.get_traceback = lambda: "traceback"
    def get_attr(path):
        import importlib
        mod, _, attr = path.rpartition(".")
        return getattr(importlib.import_module(mod), attr)
    m.get_attr = get_attr

    def throw(msg, exc=ValidationError, title=None):
        raise exc(msg)
    m.throw = throw

    def log_error(title=None, message=None):
        SITE.errors.append(f"{title}: {message}")
    m.log_error = log_error
    m._ = lambda s: s
    utils = types.ModuleType("frappe.utils")
    utils.now = lambda: "2026-10-13 10:00:00"
    utils.now_datetime = datetime.datetime.now
    utils.flt = lambda v, p=None: float(v or 0)
    utils.cint = lambda v: int(float(v or 0))
    utils.getdate = lambda v=None: datetime.date.fromisoformat(str(v)[:10]) if v else datetime.date.today()
    utils.nowdate = lambda: "2026-10-13"
    import calendar as _cal

    def _d(v):
        return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])

    def add_months(v, n):
        d = _d(v)
        y, mth = divmod(d.month - 1 + n, 12)
        y += d.year
        return d.replace(year=y, month=mth + 1, day=min(d.day, _cal.monthrange(y, mth + 1)[1]))
    utils.add_months = add_months
    utils.add_days = lambda v, n: _d(v) + datetime.timedelta(days=n)
    utils.get_first_day = lambda v: _d(v).replace(day=1)
    utils.get_last_day = lambda v: _d(v).replace(day=_cal.monthrange(_d(v).year, _d(v).month)[1])
    utils.now_datetime = datetime.datetime.now
    utils.get_url = lambda: "https://test.site"
    m.utils = utils
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Document
    pdfmod = types.ModuleType("frappe.utils.pdf")
    pdfmod.get_pdf = lambda html, options=None: b"%PDF-fake"
    sys.modules["frappe.utils.pdf"] = pdfmod
    sys.modules.update({"frappe": m, "frappe.utils": utils, "frappe.model": model,
                        "frappe.model.document": document})
    for k in [k for k in sys.modules if k.startswith("neotec_insight")]:
        del sys.modules[k]
    return SITE


def uninstall():
    for k in [k for k in sys.modules if k == "frappe" or k.startswith("frappe.")
              or k.startswith("neotec_insight")]:
        del sys.modules[k]
