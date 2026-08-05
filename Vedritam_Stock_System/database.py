# database.py
import csv
import json
import uuid
import os
from typing import List, Dict, Any, Optional
from utils import atomic_csv_write, hash_password, current_timestamp, calculate_balance, calculate_books_required
import cache
from config import (USERS_CSV, SCHOOLS_CSV, LEDGER_CSV, AUDIT_CSV, AI_SETTINGS_JSON,
                    CATALOG_CSV, VENDORS_CSV, STANDARDS, ARTICLE_CATEGORIES)

SCHOOL_HEADERS = [
    "id", "name", "code", "location", "logo", "address", "contact",
    "academic_year", "status", "assigned_staff", "settings_json", "classes_json"
]
USER_HEADERS = [
    "username", "password_hash", "role", "fullName", "email", "school_id",
    "lastLogin", "status", "created_time", "created_by"
]
AUDIT_HEADERS = ["id", "timestamp", "username", "role", "action", "entity", "entity_id", "details"]

# The ledger mirrors the printed "STOCK REGISTER FOR TEXT BOOKS / NOTE BOOKS"
# register (22 columns) and keeps the older stock-tracking columns alongside.
LEDGER_HEADERS = [
    "id", "school_id", "class_id", "standard",
    # vendor block (cols 2-5)
    "vendorId", "vendor", "vendorContact", "vendorGst",
    # invoice block (cols 6-7)
    "invoiceDate", "invoiceRef",
    # article block (cols 8-10)
    "bookName", "category", "subject", "publication", "edition",
    # quantity block (cols 11-12)
    "openingBalance", "purchased",
    # money block (cols 13-18)
    "approvedRate", "baseRate", "gstAmount", "discountPercent", "discountAmount", "totalAmount",
    # issue block (cols 19-21)
    "distributed", "returned", "closingBalance",
    # legacy stock columns kept so existing dashboards keep working
    "strength", "booksRequired", "balance",
    "remarks",
    "created_by", "created_time", "modified_by", "modified_time"
]

CATALOG_HEADERS = ["standard", "category", "subject", "title", "publication", "default_qty_per_student"]
VENDOR_HEADERS = ["vendorId", "name", "contact", "gst"]

# Derived (server-computed) ledger columns the browser may never write directly.
LEDGER_DERIVED = ["balance", "booksRequired", "discountAmount", "totalAmount", "closingBalance"]


def normalize_standard(value: str) -> str:
    """Maps free text ('Class 5-A', 'std vi', 'LKG') onto a catalog standard."""
    import re as _re
    t = _re.sub(r"\s+", " ", str(value or "")).strip().upper()
    if not t:
        return "OTHERS"
    if t in STANDARDS:
        return t
    t2 = t.replace("STANDARD", "").replace("CLASS", "").replace("STD", "").strip(" :-")
    if t2 in STANDARDS:
        return t2
    if "PRE KG" in t or "PREKG" in t or "PRE-KG" in t:
        return "PRE KG"
    if t2.startswith("LKG"):
        return "LKG"
    if t2.startswith("UKG"):
        return "UKG"
    m = _re.match(r"^(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b", t2)
    if m:
        return m.group(1)
    m = _re.search(r"\b(1[0-2]|[1-9])\b", t2)
    if m:
        roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
        return roman[int(m.group(1)) - 1]
    return "OTHERS"


# --- Role model -----------------------------------------------------------
# super_admin : manages everything, every school
# staff       : manages only the school(s) assigned to the account
# user        : can only reach their own records inside their own school
SUPER_ADMIN = "super_admin"
STAFF = "staff"
USER = "user"
VALID_ROLES = (SUPER_ADMIN, STAFF, USER)
VALID_STATUSES = ("Active", "Pending", "Disabled")
SCHOOL_STATUSES = ("Active", "Inactive", "Archived")

# Legacy role names kept working so existing users.csv files still load.
ROLE_ALIASES = {
    "admin": SUPER_ADMIN,
    "administrator": SUPER_ADMIN,
    "superadmin": SUPER_ADMIN,
    "super_admin": SUPER_ADMIN,
    "staff": STAFF,
    "school": USER,
    "user": USER,
}


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(str(role or "").strip().lower(), USER)


def init_db():
    if not os.path.exists(USERS_CSV):
        atomic_csv_write(USERS_CSV, USER_HEADERS, [{
            "username": "admin",
            "password_hash": hash_password("admin123"),
            "role": SUPER_ADMIN,
            "fullName": "System Administrator",
            "email": "",
            "school_id": "",
            "lastLogin": current_timestamp(),
            "status": "Active",
            "created_time": current_timestamp(),
            "created_by": "system",
        }])
    else:
        migrate_users_csv()

    if not os.path.exists(AUDIT_CSV):
        atomic_csv_write(AUDIT_CSV, AUDIT_HEADERS, [])

    if not os.path.exists(SCHOOLS_CSV):
        # The schools file starts empty; schools are created by a super admin.
        atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, [])
    else:
        migrate_schools_csv()

    if not os.path.exists(LEDGER_CSV):
        # The ledger file starts empty; rows are created from stock entries.
        atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, [])
    else:
        migrate_ledger_csv()

    if not os.path.exists(VENDORS_CSV):
        atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, [])
    if not os.path.exists(CATALOG_CSV):
        atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, [])


def migrate_ledger_csv():
    """Upgrades an older ledger.csv to the full 22-column stock register,
    backfilling `standard` from the school's class name."""
    rows = read_csv(LEDGER_CSV)
    if not rows:
        atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, [])
        return
    if all(h in rows[0] for h in LEDGER_HEADERS):
        return

    # class_id -> class name lookup, per school, for the standard backfill.
    class_names = {}
    try:
        for s in read_csv(SCHOOLS_CSV):
            for c in json.loads(s.get("classes_json", "[]") or "[]"):
                class_names[(str(s.get("id")), str(c.get("id")))] = c.get("name", "")
    except Exception:
        pass

    upgraded = []
    for r in rows:
        row = {h: r.get(h, "") for h in LEDGER_HEADERS}
        if not row.get("standard"):
            row["standard"] = normalize_standard(
                class_names.get((str(r.get("school_id")), str(r.get("class_id"))), "")
            )
        if not row.get("openingBalance"):
            row["openingBalance"] = "0"
        for money in ["approvedRate", "baseRate", "gstAmount", "discountPercent",
                      "discountAmount", "totalAmount"]:
            if not row.get(money):
                row[money] = "0"
        row["closingBalance"] = str(
            _num(row.get("openingBalance")) + _num(row.get("purchased"))
            - _num(row.get("distributed")) - _num(row.get("returned"))
        )
        upgraded.append(row)
    atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, upgraded)


# --- Catalog and vendor masters ---------------------------------------------
def get_catalog(standard: str = "", category: str = "") -> List[Dict]:
    """Master list of titles / notebook categories seeded from the school's
    Text Book & Note Book requirement sheets. standard='ALL' returns everything."""
    if not os.path.exists(CATALOG_CSV):
        return []
    rows = read_csv(CATALOG_CSV)
    std = str(standard or "").strip().upper()
    cat = str(category or "").strip().upper()
    if std and std != "ALL":
        std = normalize_standard(std)
        rows = [r for r in rows if str(r.get("standard", "")).upper() == std]
    if cat and cat != "ALL":
        rows = [r for r in rows if str(r.get("category", "")).upper() == cat]
    return rows


def get_catalog_standards() -> List[str]:
    return list(STANDARDS)


def get_article_categories() -> List[str]:
    return list(ARTICLE_CATEGORIES)


def get_catalog_publications() -> List[str]:
    pubs = {str(r.get("publication", "")).strip() for r in get_catalog()}
    return sorted(p for p in pubs if p)


def get_vendors() -> List[Dict]:
    if not os.path.exists(VENDORS_CSV):
        return []
    return read_csv(VENDORS_CSV)


def upsert_vendor(vendor_id: str, name: str, contact: str = "", gst: str = ""):
    """Keeps the vendor master in step with what is typed into the ledger."""
    vendor_id = str(vendor_id or "").strip()
    name = str(name or "").strip()
    if not vendor_id and not name:
        return
    vendors = get_vendors()
    key = vendor_id or name
    for v in vendors:
        if (v.get("vendorId") or v.get("name")) == key:
            if name:
                v["name"] = name
            if contact:
                v["contact"] = contact
            if gst:
                v["gst"] = gst
            atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, vendors)
            return
    vendors.append({"vendorId": vendor_id, "name": name, "contact": contact, "gst": gst})
    atomic_csv_write(VENDORS_CSV, VENDOR_HEADERS, vendors)



def migrate_users_csv():
    """Upgrades an older users.csv (username,password_hash,role,lastLogin,status)
    to the extended schema without losing any existing accounts."""
    rows = read_csv(USERS_CSV)
    if not rows:
        return
    needs_role_fix = any(r.get("role") not in VALID_ROLES for r in rows)
    if all(h in rows[0] for h in USER_HEADERS) and not needs_role_fix:
        return
    upgraded = []
    for r in rows:
        upgraded.append({
            "username": r.get("username", ""),
            "password_hash": r.get("password_hash", ""),
            "role": normalize_role(r.get("role", "")),
            "fullName": r.get("fullName", "") or r.get("username", ""),
            "email": r.get("email", ""),
            "school_id": r.get("school_id", ""),
            "lastLogin": r.get("lastLogin", ""),
            "status": r.get("status", "Active") or "Active",
            "created_time": r.get("created_time", "") or current_timestamp(),
            "created_by": r.get("created_by", "") or "system",
        })
    atomic_csv_write(USERS_CSV, USER_HEADERS, upgraded)


def migrate_schools_csv():
    """Adds the multi-school columns (logo, address, contact, academic year,
    status, assigned staff, settings) to an older schools.csv."""
    rows = read_csv(SCHOOLS_CSV)
    if not rows:
        return
    if all(h in rows[0] for h in SCHOOL_HEADERS):
        return
    upgraded = []
    for r in rows:
        upgraded.append({
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "code": r.get("code", ""),
            "location": r.get("location", ""),
            "logo": r.get("logo", ""),
            "address": r.get("address", "") or r.get("location", ""),
            "contact": r.get("contact", ""),
            "academic_year": r.get("academic_year", ""),
            "status": r.get("status", "") or "Active",
            "assigned_staff": r.get("assigned_staff", ""),
            "settings_json": r.get("settings_json", "") or "{}",
            "classes_json": r.get("classes_json", "[]") or "[]",
        })
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, upgraded)


def read_csv(filepath: str) -> List[Dict[str, Any]]:
    """Cached CSV read. Every write path goes through atomic_csv_write, which
    drops the matching cache key, so callers never observe stale rows."""
    key = "file:" + filepath
    hit = cache.get(key)
    if hit is not None:
        return [dict(r) for r in hit]
    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []
    cache.set(key, rows, ttl=10)
    return [dict(r) for r in rows]


def get_audit_log_page(limit: int = 50, offset: int = 0, username: str = "",
                       action: str = "") -> Dict[str, Any]:
    """Paginated activity log — avoids shipping the whole file to the browser."""
    rows = get_audit_log(limit=100000, username=username, action=action)
    total = len(rows)
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    return {"items": rows[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset, "has_more": offset + limit < total}


# --- Audit Trail ---------------------------------------------------------
def log_action(username: str, role: str, action: str, entity: str = "", entity_id: str = "", details: str = ""):
    """Appends a single immutable-ish row to the activity log."""
    try:
        rows = read_csv(AUDIT_CSV)
        rows.append({
            "id": f"A_{uuid.uuid4().hex[:10]}",
            "timestamp": current_timestamp(),
            "username": username or "anonymous",
            "role": role or "",
            "action": action,
            "entity": entity,
            "entity_id": str(entity_id),
            "details": details,
        })
        # Bound the log size so the CSV does not grow without limit.
        if len(rows) > 20000:
            rows = rows[-20000:]
        atomic_csv_write(AUDIT_CSV, AUDIT_HEADERS, rows)
    except Exception as e:
        print(f"[audit] failed to write log entry: {e}")


def get_audit_log(limit: int = 200, username: str = "", action: str = "", school_id: str = "") -> List[Dict]:
    rows = read_csv(AUDIT_CSV)
    if username:
        rows = [r for r in rows if r.get("username", "").lower() == username.lower()]
    if action:
        rows = [r for r in rows if r.get("action", "") == action]
    if school_id:
        rows = [r for r in rows if str(r.get("entity_id", "")).startswith(str(school_id) + ":")
                or str(r.get("entity_id", "")) == str(school_id)]
    rows.reverse()  # newest first
    return rows[:max(1, min(limit, 2000))]


# --- Users Logic ---------------------------------------------------------
def _sanitize_user(u: Dict) -> Dict:
    online = {name.lower() for name in presence_snapshot().get("usernames", [])}
    return {
        "username": u.get("username", ""),
        "role": normalize_role(u.get("role", "")),
        "fullName": u.get("fullName", ""),
        "email": u.get("email", ""),
        "school_id": u.get("school_id", ""),
        "school_name": _school_name(u.get("school_id", "")),
        "lastLogin": u.get("lastLogin", ""),
        "status": u.get("status", ""),
        "online": u.get("username", "").lower() in online,
        "created_time": u.get("created_time", ""),
        "created_by": u.get("created_by", ""),
    }


def _school_name(school_id) -> str:
    if not school_id:
        return ""
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id):
            return s["name"]
    return ""


def get_user_raw(username: str) -> Optional[Dict]:
    for u in read_csv(USERS_CSV):
        if u['username'].lower() == str(username).lower():
            return u
    return None


def get_user_by_username(username: str) -> Optional[Dict]:
    """Only returns an account that is allowed to authenticate."""
    u = get_user_raw(username)
    if u and u.get('status') == 'Active':
        return u
    return None


def get_all_users() -> List[Dict]:
    return [_sanitize_user(u) for u in read_csv(USERS_CSV)]


def get_user_profile(username: str) -> Optional[Dict]:
    u = get_user_raw(username)
    return _sanitize_user(u) if u else None


def _write_users(users: List[Dict]):
    atomic_csv_write(USERS_CSV, USER_HEADERS, users)


def update_user_login(username: str):
    users = read_csv(USERS_CSV)
    for u in users:
        if u['username'] == username:
            u['lastLogin'] = current_timestamp()
    _write_users(users)


def create_user(username: str, password: str, role: str, full_name: str = "", email: str = "",
                school_id: str = "", status: str = "Active", created_by: str = "system") -> Dict:
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters long.")
    if any(c in username for c in " ,\t\n"):
        raise ValueError("Username cannot contain spaces or commas.")
    role = normalize_role(role)
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}.")
    if status not in VALID_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(VALID_STATUSES)}.")
    if get_user_raw(username):
        raise ValueError(f"Username '{username}' is already taken.")
    if school_id and not _school_name(school_id):
        raise ValueError("Selected school does not exist.")

    users = read_csv(USERS_CSV)
    record = {
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "fullName": (full_name or "").strip(),
        "email": (email or "").strip(),
        "school_id": str(school_id or ""),
        "lastLogin": "",
        "status": status,
        "created_time": current_timestamp(),
        "created_by": created_by,
    }
    users.append(record)
    _write_users(users)
    return _sanitize_user(record)


def update_user(username: str, role: str = None, status: str = None, full_name: str = None,
                email: str = None, school_id: str = None) -> Dict:
    users = read_csv(USERS_CSV)
    target = next((u for u in users if u['username'].lower() == str(username).lower()), None)
    if not target:
        raise ValueError("User not found.")

    if role is not None:
        role = normalize_role(role)
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}.")
        if target['role'] == SUPER_ADMIN and role != SUPER_ADMIN and _count_active_admins(users) <= 1:
            raise ValueError("Cannot remove the last remaining administrator.")
        target['role'] = role
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(VALID_STATUSES)}.")
        if target['role'] == SUPER_ADMIN and status != 'Active' and _count_active_admins(users) <= 1:
            raise ValueError("Cannot disable the last remaining administrator.")
        target['status'] = status
    if full_name is not None:
        target['fullName'] = full_name.strip()
    if email is not None:
        target['email'] = email.strip()
    if school_id is not None:
        if school_id and not _school_name(school_id):
            raise ValueError("Selected school does not exist.")
        target['school_id'] = str(school_id)

    _write_users(users)
    return _sanitize_user(target)


def _count_active_admins(users: List[Dict]) -> int:
    return len([u for u in users if normalize_role(u.get('role')) == SUPER_ADMIN and u.get('status') == 'Active'])


def set_password(username: str, new_password: str):
    users = read_csv(USERS_CSV)
    target = next((u for u in users if u['username'].lower() == str(username).lower()), None)
    if not target:
        raise ValueError("User not found.")
    target['password_hash'] = hash_password(new_password)
    _write_users(users)


def delete_user(username: str):
    users = read_csv(USERS_CSV)
    target = next((u for u in users if u['username'].lower() == str(username).lower()), None)
    if not target:
        raise ValueError("User not found.")
    if normalize_role(target.get('role')) == SUPER_ADMIN and _count_active_admins(users) <= 1:
        raise ValueError("Cannot delete the last remaining administrator.")
    users = [u for u in users if u['username'].lower() != str(username).lower()]
    _write_users(users)


# --- Schools & Classes Logic ---------------------------------------------
def _staff_list(raw: str) -> List[str]:
    """assigned_staff is stored as a simple pipe-separated username list."""
    return [p.strip() for p in str(raw or "").split("|") if p.strip()]


def _staff_raw(names: List[str]) -> str:
    seen, out = set(), []
    for n in names:
        key = str(n or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(n).strip())
    return "|".join(out)


def _school_public(s: Dict) -> Dict:
    try:
        settings = json.loads(s.get("settings_json") or "{}")
    except Exception:
        settings = {}
    return {
        "id": int(s["id"]),
        "name": s.get("name", ""),
        "code": s.get("code", ""),
        "location": s.get("location", ""),
        "logo": s.get("logo", ""),
        "address": s.get("address", ""),
        "contact": s.get("contact", ""),
        "academic_year": s.get("academic_year", ""),
        "status": s.get("status", "") or "Active",
        "assigned_staff": _staff_list(s.get("assigned_staff", "")),
        "settings": settings if isinstance(settings, dict) else {},
    }


def get_all_schools(school_id_filter: str = "", allowed_ids: Optional[List[str]] = None) -> List[Dict]:
    """Returns schools visible to the caller.

    school_id_filter -- single-school scope (a 'user' account).
    allowed_ids      -- explicit whitelist (a 'staff' account); None means no limit.
    """
    rows = read_csv(SCHOOLS_CSV)
    if school_id_filter:
        rows = [s for s in rows if str(s["id"]) == str(school_id_filter)]
    if allowed_ids is not None:
        wanted = {str(i) for i in allowed_ids}
        rows = [s for s in rows if str(s["id"]) in wanted]
    return [_school_public(s) for s in rows]


def get_school(school_id) -> Optional[Dict]:
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id):
            return _school_public(s)
    return None


def school_ids_for_staff(username: str) -> List[str]:
    """Every school this staff account manages: the school on their user record
    plus any school that lists them in assigned_staff."""
    ids = []
    user = get_user_raw(username)
    own = str((user or {}).get("school_id", "") or "")
    if own:
        ids.append(own)
    uname = str(username or "").strip().lower()
    for s in read_csv(SCHOOLS_CSV):
        if uname in [n.lower() for n in _staff_list(s.get("assigned_staff", ""))]:
            ids.append(str(s["id"]))
    return sorted(set(ids), key=lambda v: int(v) if str(v).isdigit() else 0)


def get_classes_for_school(school_id: int) -> List[Dict]:
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id):
            return json.loads(s.get("classes_json", "[]") or "[]")
    return []


def add_school(name: str, code: str = "", location: str = "", logo: str = "", address: str = "",
               contact: str = "", academic_year: str = "", status: str = "Active",
               assigned_staff: Optional[List[str]] = None,
               settings: Optional[Dict[str, Any]] = None) -> Dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("School name is required.")
    status = (status or "Active").strip() or "Active"
    if status not in SCHOOL_STATUSES:
        raise ValueError(f"School status must be one of: {', '.join(SCHOOL_STATUSES)}.")
    schools = read_csv(SCHOOLS_CSV)
    for s in schools:
        if s["name"].strip().lower() == name.lower():
            raise ValueError(f"School '{name}' already exists.")
    new_id = str(max([int(s["id"]) for s in schools] + [0]) + 1)
    record = {
        "id": new_id,
        "name": name,
        "code": (code or "").strip(),
        "location": (location or "").strip(),
        "logo": (logo or "").strip(),
        "address": (address or "").strip(),
        "contact": (contact or "").strip(),
        "academic_year": (academic_year or "").strip(),
        "status": status,
        "assigned_staff": _staff_raw(assigned_staff or []),
        "settings_json": json.dumps(settings or {}),
        "classes_json": "[]",
    }
    schools.append(record)
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return _school_public(record)


def update_school(school_id, **fields) -> Dict:
    """Updates any School attribute. Only supplied (non-None) fields change."""
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target:
        raise ValueError("School not found.")

    name = fields.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("School name cannot be empty.")
        if any(s["name"].strip().lower() == name.lower() and str(s["id"]) != str(school_id) for s in schools):
            raise ValueError(f"School '{name}' already exists.")
        target["name"] = name

    for key in ("code", "location", "logo", "address", "contact", "academic_year"):
        if fields.get(key) is not None:
            target[key] = str(fields[key]).strip()

    if fields.get("status") is not None:
        status = str(fields["status"]).strip() or "Active"
        if status not in SCHOOL_STATUSES:
            raise ValueError(f"School status must be one of: {', '.join(SCHOOL_STATUSES)}.")
        target["status"] = status

    if fields.get("assigned_staff") is not None:
        names = fields["assigned_staff"]
        if isinstance(names, str):
            names = [n for n in names.replace(",", "|").split("|")]
        known = {u["username"].lower(): u["username"] for u in read_csv(USERS_CSV)}
        cleaned = []
        for n in names:
            key = str(n or "").strip().lower()
            if not key:
                continue
            if key not in known:
                raise ValueError(f"Staff account '{n}' does not exist.")
            cleaned.append(known[key])
        target["assigned_staff"] = _staff_raw(cleaned)

    if fields.get("settings") is not None:
        settings = fields["settings"]
        if not isinstance(settings, dict):
            raise ValueError("School settings must be an object.")
        target["settings_json"] = json.dumps(settings)

    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return _school_public(target)


def delete_school(school_id: int):
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target:
        raise ValueError("School not found.")
    if json.loads(target.get("classes_json", "[]") or "[]"):
        raise ValueError("Cannot delete school: It contains active classes. Please delete classes first to prevent orphaned records.")
    if any(str(u.get("school_id", "")) == str(school_id) for u in read_csv(USERS_CSV)):
        raise ValueError("Cannot delete school: accounts are still assigned to it. Reassign those accounts first.")
    schools = [s for s in schools if str(s["id"]) != str(school_id)]
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)


def add_class_to_school(school_id: int, name: str, strength: int) -> Dict:
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target: 
        raise ValueError("School not found.")
    classes = json.loads(target.get("classes_json", "[]"))
    if any(c["name"].lower() == name.lower() for c in classes): 
        raise ValueError("Class already exists.")
    new_class = {"id": max([int(c["id"]) for c in classes] + [0]) + 1, "name": name, "strength": strength}
    classes.append(new_class)
    target["classes_json"] = json.dumps(classes)
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return new_class

def delete_class(school_id: int, class_id: int):
    ledger = read_csv(LEDGER_CSV)
    if any(str(r["class_id"]) == str(class_id) for r in ledger):
        raise ValueError("Cannot delete class: It contains existing ledger records. Preserve data integrity by clearing the ledger first.")
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if target:
        classes = json.loads(target.get("classes_json", "[]"))
        target["classes_json"] = json.dumps([c for c in classes if str(c["id"]) != str(class_id)])
        atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)

def update_class_strength(school_id: int, class_id: int, new_strength: int):
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target: 
        raise ValueError("School not found.")
    
    classes = json.loads(target.get("classes_json", "[]"))
    class_updated = False
    
    for c in classes:
        if str(c["id"]) == str(class_id):
            c["strength"] = new_strength
            class_updated = True
            break
            
    if not class_updated: 
        raise ValueError("Class not found.")
        
    target["classes_json"] = json.dumps(classes)
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)

# --- Ledger Logic --------------------------------------------------------
LEDGER_INT_FIELDS = ['purchased', 'distributed', 'returned', 'balance', 'booksRequired',
                     'strength', 'openingBalance', 'closingBalance']
LEDGER_FLOAT_FIELDS = ['approvedRate', 'baseRate', 'gstAmount', 'discountPercent',
                       'discountAmount', 'totalAmount']


def _dec(v) -> float:
    try:
        return round(float(str(v).replace(",", "").strip() or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _cast_ledger_row(r: Dict) -> Dict:
    for f in LEDGER_INT_FIELDS:
        r[f] = _num(r.get(f))
    for f in LEDGER_FLOAT_FIELDS:
        r[f] = _dec(r.get(f))
    r["standard"] = str(r.get("standard") or "OTHERS")
    return r


def get_ledger_records(school_id: int, class_id: int, created_by: str = "") -> List[Dict]:
    """created_by narrows the rows to the ones a plain 'user' account owns."""
    ledger = read_csv(LEDGER_CSV)
    result = [r for r in ledger if str(r["school_id"]) == str(school_id) and str(r["class_id"]) == str(class_id)]
    if created_by:
        result = [r for r in result if str(r.get("created_by", "")).lower() == created_by.lower()]
    return [_cast_ledger_row(r) for r in result]


def get_ledger_by_standard(school_id: int, standard: str, created_by: str = "") -> List[Dict]:
    """Rows for one standard, or every standard when standard is 'ALL'.
    Ordered by standard so the 'All standards' view groups naturally."""
    ledger = read_csv(LEDGER_CSV)
    result = [r for r in ledger if str(r["school_id"]) == str(school_id)]
    std = str(standard or "").strip().upper()
    if std and std != "ALL":
        std = normalize_standard(std)
        result = [r for r in result if normalize_standard(r.get("standard", "")) == std]
    if created_by:
        result = [r for r in result if str(r.get("created_by", "")).lower() == created_by.lower()]
    result = [_cast_ledger_row(r) for r in result]
    order = {s: i for i, s in enumerate(STANDARDS)}
    result.sort(key=lambda r: (order.get(r.get("standard"), 99), str(r.get("category", "")),
                               str(r.get("bookName", ""))))
    return result


def standard_strength(school_id: int, standard: str) -> int:
    """Total student strength recorded for a standard, summed across its
    sections (Class 5-A, Class 5-B ... all map to standard V)."""
    std = normalize_standard(standard)
    total = 0
    for c in get_classes_for_school(school_id):
        if normalize_standard(c.get("name", "")) == std:
            total += _num(c.get("strength"))
    return total


def rows_not_owned_by(record_ids: List[str], username: str) -> List[str]:
    """Ledger row ids in the list that were not created by this username."""
    wanted = {str(i) for i in record_ids}
    owned_check = []
    for r in read_csv(LEDGER_CSV):
        if str(r.get("id")) in wanted and str(r.get("created_by", "")).lower() != str(username).lower():
            owned_check.append(str(r.get("id")))
    return owned_check


def sync_ledger_records(school_id: int, class_id: int, updates: List[Dict], deletes: List[str],
                        username: str, standard: str = ""):
    """Persists ledger edits. Rows are keyed on the standard (PRE KG .. XII);
    class_id is kept for backwards compatibility with older records."""
    default_standard = normalize_standard(standard) if standard else ""

    # Strength default: the standard's recorded strength, else the legacy class.
    if default_standard:
        default_class_strength = standard_strength(school_id, default_standard)
    else:
        target_class = next((c for c in get_classes_for_school(school_id) if str(c["id"]) == str(class_id)), None)
        if not target_class:
            raise ValueError("Class not found.")
        default_class_strength = _num(target_class["strength"])
        default_standard = normalize_standard(target_class.get("name", ""))

    ledger = read_csv(LEDGER_CSV)
    delete_set = set(deletes)
    deleted_names = [r.get("bookName", r.get("id")) for r in ledger if str(r["id"]) in delete_set]
    ledger = [row for row in ledger if str(row["id"]) not in delete_set]
    update_dict = {str(u["id"]): u for u in updates}

    changed_books = []
    touched_vendors = []

    # Validate the submitted row modifications.
    def process_row(row_data, incoming_mod):
        protected = ['id', 'school_id', 'class_id', 'created_by', 'created_time'] + LEDGER_DERIVED
        for key in LEDGER_HEADERS:
            if key in incoming_mod and key not in protected:
                row_data[key] = incoming_mod[key]

        name = row_data.get('bookName') or 'Unknown'
        row_data["standard"] = normalize_standard(row_data.get("standard") or default_standard)
        cat = str(row_data.get("category") or "").strip().upper()
        if cat and cat not in ARTICLE_CATEGORIES:
            # free-text categories are allowed, they are just not normalised
            cat = str(row_data.get("category")).strip()
        row_data["category"] = cat

        p = _num(row_data.get("purchased"))
        d = _num(row_data.get("distributed"))
        r = _num(row_data.get("returned"))
        ob = _num(row_data.get("openingBalance"))

        row_str = row_data.get("strength")
        if row_str is not None and str(row_str).strip().isdigit():
            row_strength = int(row_str)
        else:
            row_strength = default_class_strength

        if min(p, d, r, ob, row_strength) < 0:
            raise ValueError(f"Stock quantities and strength cannot be negative for '{name}'.")

        balance = calculate_balance(p, d, r)
        if balance < 0:
            raise ValueError(f"Transaction rejected: Distributing {d} books when only {p+r} are available creates a negative balance for '{name}'.")

        closing = ob + p - d - r
        if closing < 0:
            raise ValueError(f"Closing balance for '{name}' would be negative: opening {ob} + purchased {p} cannot cover {d} issued and {r} returned.")

        base = _dec(row_data.get("baseRate"))
        gst = _dec(row_data.get("gstAmount"))
        disc_pct = _dec(row_data.get("discountPercent"))
        if disc_pct < 0 or disc_pct > 100:
            raise ValueError(f"Discount % for '{name}' must be between 0 and 100.")
        if base < 0 or gst < 0:
            raise ValueError(f"Rates and GST for '{name}' cannot be negative.")

        qty = p if p > 0 else 1
        discount_amount = round(base * qty * disc_pct / 100, 2)
        total_amount = round(base * qty + gst - discount_amount, 2)

        row_data["strength"] = str(row_strength)
        row_data["openingBalance"] = str(ob)
        row_data["balance"] = str(balance)
        row_data["closingBalance"] = str(closing)
        row_data["booksRequired"] = str(calculate_books_required(row_strength, p))
        row_data["approvedRate"] = str(_dec(row_data.get("approvedRate")))
        row_data["baseRate"] = str(base)
        row_data["gstAmount"] = str(gst)
        row_data["discountPercent"] = str(disc_pct)
        row_data["discountAmount"] = str(discount_amount)
        row_data["totalAmount"] = str(total_amount)
        row_data["modified_by"] = username
        row_data["modified_time"] = current_timestamp()

        if row_data.get("vendor") or row_data.get("vendorId"):
            touched_vendors.append((row_data.get("vendorId", ""), row_data.get("vendor", ""),
                                    row_data.get("vendorContact", ""), row_data.get("vendorGst", "")))

    # Apply updates to existing rows.
    for row in ledger:
        rid = str(row["id"])
        if rid in update_dict:
            process_row(row, update_dict[rid])
            changed_books.append(f"edited '{row.get('bookName', rid)}'")
            del update_dict[rid]

    # Append newly added rows.
    for uid, new_row in update_dict.items():
        if not str(uid).startswith("new_"):
            continue
        record = {h: "" for h in LEDGER_HEADERS}
        record.update({
            "id": f"L_{uuid.uuid4().hex[:8]}",
            "school_id": str(school_id),
            "class_id": str(class_id or ""),
            "created_by": username,
            "created_time": current_timestamp()
        })
        process_row(record, new_row)
        changed_books.append(f"added '{record.get('bookName', record['id'])}'")
        ledger.insert(0, record)

    atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, ledger)

    for vid, vname, vcontact, vgst in touched_vendors:
        try:
            upsert_vendor(vid, vname, vcontact, vgst)
        except Exception:
            pass

    for name in deleted_names:
        changed_books.append(f"deleted '{name}'")
    return changed_books



def school_id_for_ledger_row(record_id: str) -> str:
    for r in read_csv(LEDGER_CSV):
        if str(r["id"]) == str(record_id):
            return str(r["school_id"])
    return ""


# --- AI assistant settings (admin-managed, stored server-side) ----------------
AI_DEFAULTS = {"apiKey": "", "apiBase": "https://api.openai.com/v1", "model": "gpt-4o-mini"}


def get_ai_settings() -> Dict[str, str]:
    """Full settings including the secret key. Never return this to the browser."""
    data = dict(AI_DEFAULTS)
    try:
        with open(AI_SETTINGS_JSON, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for k in AI_DEFAULTS:
                if stored.get(k):
                    data[k] = str(stored[k])
    except Exception:
        pass
    return data


def save_ai_settings(api_key: Optional[str], api_base: str, model: str) -> Dict[str, str]:
    current = get_ai_settings()
    data = {
        # An empty apiKey means "keep the existing one" so the admin can edit
        # the base URL/model without retyping the secret.
        "apiKey": (api_key or "").strip() or current["apiKey"],
        "apiBase": (api_base or "").strip() or AI_DEFAULTS["apiBase"],
        "model": (model or "").strip() or AI_DEFAULTS["model"],
    }
    with open(AI_SETTINGS_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        os.chmod(AI_SETTINGS_JSON, 0o600)
    except Exception:
        pass
    return data


def clear_ai_settings() -> None:
    try:
        os.remove(AI_SETTINGS_JSON)
    except FileNotFoundError:
        pass


# --- Dashboard analytics -------------------------------------------------
LOW_STOCK_THRESHOLD = 50
_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _num(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def get_dashboard_stats(school_id_filter: str = "") -> Dict[str, Any]:
    """Real analytics computed from schools.csv, ledger.csv and audit.csv.
    No demo or placeholder numbers are used anywhere."""
    from datetime import datetime

    schools = read_csv(SCHOOLS_CSV)
    if school_id_filter:
        schools = [s for s in schools if str(s["id"]) == str(school_id_filter)]
    school_ids = {str(s["id"]) for s in schools}

    ledger = [r for r in read_csv(LEDGER_CSV) if str(r.get("school_id")) in school_ids]

    total_purchased = sum(_num(r.get("purchased")) for r in ledger)
    total_distributed = sum(_num(r.get("distributed")) for r in ledger)
    total_returned = sum(_num(r.get("returned")) for r in ledger)
    total_balance = sum(_num(r.get("balance")) for r in ledger)
    total_required = sum(_num(r.get("booksRequired")) for r in ledger)
    low_rows = [r for r in ledger if _num(r.get("balance")) < LOW_STOCK_THRESHOLD]

    today = datetime.now().strftime("%Y-%m-%d")
    added_today = sum(_num(r.get("purchased")) for r in ledger
                      if str(r.get("created_time", "")).startswith(today))

    # ---- monthly trend (last 6 calendar months, from ledger timestamps) ----
    now = datetime.now()
    months = []
    y, m = now.year, now.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    buckets = {km: {"issued": 0, "received": 0} for km in months}
    for r in ledger:
        ts = str(r.get("modified_time") or r.get("created_time") or "")[:7]
        try:
            key = (int(ts[:4]), int(ts[5:7]))
        except ValueError:
            continue
        if key in buckets:
            buckets[key]["issued"] += _num(r.get("distributed"))
            buckets[key]["received"] += _num(r.get("purchased"))

    monthly = {
        "labels": [_MONTH_NAMES[m - 1] for (y, m) in months],
        "issued": [buckets[k]["issued"] for k in months],
        "received": [buckets[k]["received"] for k in months],
    }

    # ---- per-school rollups ----
    per_school = []
    for s in schools:
        sid = str(s["id"])
        rows = [r for r in ledger if str(r.get("school_id")) == sid]
        try:
            classes = json.loads(s.get("classes_json") or "[]")
        except (ValueError, TypeError):
            classes = []
        per_school.append({
            "id": _num(sid),
            "name": s.get("name", ""),
            "code": s.get("code", ""),
            "students": sum(_num(c.get("strength")) for c in classes),
            "purchased": sum(_num(r.get("purchased")) for r in rows),
            "issued": sum(_num(r.get("distributed")) for r in rows),
            "balance": sum(_num(r.get("balance")) for r in rows),
        })
    per_school.sort(key=lambda x: x["issued"], reverse=True)

    # ---- recent activity from the audit trail ----
    tone_for = {
        "LOGIN": "blue", "LOGOUT": "blue",
        "SCHOOL_CREATE": "blue", "SCHOOL_DELETE": "red",
        "CLASS_CREATE": "blue", "CLASS_DELETE": "red",
        "LEDGER_SYNC": "green", "LEDGER_DELETE": "red",
        "USER_CREATE": "blue", "USER_DELETE": "red",
    }
    activity = []
    for r in get_audit_log(limit=8, school_id=school_id_filter if school_id_filter else ""):
        activity.append({
            "tone": tone_for.get(r.get("action", ""), "amber"),
            "text": r.get("details") or r.get("action", ""),
            "meta": f"{r.get('timestamp', '')} · {r.get('username', '')}",
        })

    return {
        "kpis": {
            "books": {"value": total_purchased,
                      "delta": f"+{added_today} added today" if added_today else "no additions today"},
            "schools": {"value": len(schools),
                        "delta": f"{len(schools)} active"},
            "balance": {"value": total_balance,
                        "delta": f"{total_distributed} issued · {total_returned} returned"},
            "lowStock": {"value": len(low_rows),
                         "delta": f"{total_required} books required"},
        },
        "monthly": monthly,
        "comparison": [{"name": s["name"], "value": s["issued"]} for s in per_school[:5]],
        "activity": activity,
        "schools": per_school,
    }


# --- Presence (in-memory heartbeat) ------------------------------------------
_PRESENCE: Dict[str, float] = {}


def record_presence(username: str) -> None:
    import time as _t
    _PRESENCE[str(username or "").lower()] = _t.time()


def presence_snapshot(window_seconds: int = 120) -> Dict[str, Any]:
    """Return the list of usernames considered online (a heartbeat in the
    last `window_seconds`) plus the total count."""
    import time as _t
    now = _t.time()
    online = [u for u, ts in list(_PRESENCE.items()) if (now - ts) <= window_seconds]
    return {"count": len(online), "usernames": online}


def schools_created_by(username: str) -> int:
    """How many schools were added by this account (from the audit log)."""
    n = 0
    for r in read_csv(AUDIT_CSV):
        if r.get("action") == "SCHOOL_CREATE" and (r.get("username") or "").lower() == (username or "").lower():
            n += 1
    return n

