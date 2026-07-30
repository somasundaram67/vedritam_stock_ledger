# database.py
import csv
import json
import uuid
import os
from typing import List, Dict, Any, Optional
from utils import atomic_csv_write, hash_password, current_timestamp, calculate_balance, calculate_books_required
from config import USERS_CSV, SCHOOLS_CSV, LEDGER_CSV, AUDIT_CSV, AI_SETTINGS_JSON

SCHOOL_HEADERS = ["id", "name", "code", "location", "classes_json"]
USER_HEADERS = [
    "username", "password_hash", "role", "fullName", "email", "school_id",
    "lastLogin", "status", "created_time", "created_by"
]
AUDIT_HEADERS = ["id", "timestamp", "username", "role", "action", "entity", "entity_id", "details"]
LEDGER_HEADERS = [
    "id", "school_id", "class_id", "bookName", "subject", "publication", "vendor", "category", 
    "invoiceRef", "strength", "purchased", "distributed", "returned", "balance", "booksRequired", "remarks", 
    "created_by", "created_time", "modified_by", "modified_time"
]

VALID_ROLES = ("admin", "school", "staff")
VALID_STATUSES = ("Active", "Pending", "Disabled")


def init_db():
    if not os.path.exists(USERS_CSV):
        atomic_csv_write(USERS_CSV, USER_HEADERS, [{
            "username": "admin",
            "password_hash": hash_password("admin123"),
            "role": "admin",
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
        # The schools file starts empty; schools are created by an administrator.
        atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, [])

    if not os.path.exists(LEDGER_CSV):
        # The ledger file starts empty; rows are created from stock entries.
        atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, [])


def migrate_users_csv():
    """Upgrades an older users.csv (username,password_hash,role,lastLogin,status)
    to the extended schema without losing any existing accounts."""
    rows = read_csv(USERS_CSV)
    if not rows:
        return
    if all(h in rows[0] for h in USER_HEADERS):
        return
    upgraded = []
    for r in rows:
        upgraded.append({
            "username": r.get("username", ""),
            "password_hash": r.get("password_hash", ""),
            "role": r.get("role", "staff") or "staff",
            "fullName": r.get("fullName", "") or r.get("username", ""),
            "email": r.get("email", ""),
            "school_id": r.get("school_id", ""),
            "lastLogin": r.get("lastLogin", ""),
            "status": r.get("status", "Active") or "Active",
            "created_time": r.get("created_time", "") or current_timestamp(),
            "created_by": r.get("created_by", "") or "system",
        })
    atomic_csv_write(USERS_CSV, USER_HEADERS, upgraded)


def read_csv(filepath: str) -> List[Dict[str, Any]]:
    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


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
        "role": u.get("role", ""),
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
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}.")
        if target['role'] == 'admin' and role != 'admin' and _count_active_admins(users) <= 1:
            raise ValueError("Cannot remove the last remaining administrator.")
        target['role'] = role
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(VALID_STATUSES)}.")
        if target['role'] == 'admin' and status != 'Active' and _count_active_admins(users) <= 1:
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
    return len([u for u in users if u.get('role') == 'admin' and u.get('status') == 'Active'])


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
    if target.get('role') == 'admin' and _count_active_admins(users) <= 1:
        raise ValueError("Cannot delete the last remaining administrator.")
    users = [u for u in users if u['username'].lower() != str(username).lower()]
    _write_users(users)


# --- Schools & Classes Logic ---------------------------------------------
def get_all_schools(school_id_filter: str = "") -> List[Dict]:
    rows = read_csv(SCHOOLS_CSV)
    if school_id_filter:
        rows = [s for s in rows if str(s["id"]) == str(school_id_filter)]
    return [{"id": int(s["id"]), "name": s["name"], "code": s["code"], "location": s["location"]} for s in rows]

def get_classes_for_school(school_id: int) -> List[Dict]:
    for s in read_csv(SCHOOLS_CSV):
        if str(s["id"]) == str(school_id): 
            return json.loads(s.get("classes_json", "[]"))
    return []

def add_school(name: str, code: str, location: str) -> Dict:
    schools = read_csv(SCHOOLS_CSV)
    for s in schools:
        if s["name"].lower() == name.lower(): 
            raise ValueError(f"School '{name}' already exists.")
    new_id = str(max([int(s["id"]) for s in schools] + [0]) + 1)
    schools.append({"id": new_id, "name": name, "code": code, "location": location, "classes_json": "[]"})
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)
    return {"id": int(new_id), "name": name, "code": code, "location": location}

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

def delete_school(school_id: int):
    schools = read_csv(SCHOOLS_CSV)
    target = next((s for s in schools if str(s["id"]) == str(school_id)), None)
    if not target: 
        raise ValueError("School not found.")
    if json.loads(target.get("classes_json", "[]")):
        raise ValueError("Cannot delete school: It contains active classes. Please delete classes first to prevent orphaned records.")
    schools = [s for s in schools if str(s["id"]) != str(school_id)]
    atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, schools)

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
def get_ledger_records(school_id: int, class_id: int) -> List[Dict]:
    ledger = read_csv(LEDGER_CSV)
    result = [r for r in ledger if str(r["school_id"]) == str(school_id) and str(r["class_id"]) == str(class_id)]
    for r in result:
        for f in ['purchased', 'distributed', 'returned', 'balance', 'booksRequired', 'strength']:
            r[f] = int(r.get(f) or 0)
    return result

def sync_ledger_records(school_id: int, class_id: int, updates: List[Dict], deletes: List[str], username: str):
    # Resolve the class record used as the default strength source.
    target_class = next((c for c in get_classes_for_school(school_id) if str(c["id"]) == str(class_id)), None)
    if not target_class: 
        raise ValueError("Class not found.")
    default_class_strength = int(target_class["strength"])

    ledger = read_csv(LEDGER_CSV)
    delete_set = set(deletes)
    deleted_names = [r.get("bookName", r.get("id")) for r in ledger if str(r["id"]) in delete_set]
    ledger = [row for row in ledger if str(row["id"]) not in delete_set]
    update_dict = {str(u["id"]): u for u in updates}

    changed_books = []
    
    # Validate the submitted row modifications.
    def process_row(row_data, incoming_mod):
        for key in LEDGER_HEADERS:
            if key in incoming_mod and key not in ['id', 'school_id', 'class_id', 'balance', 'booksRequired']:
                row_data[key] = incoming_mod[key]
        try:
            p = int(row_data.get("purchased") or 0)
            d = int(row_data.get("distributed") or 0)
            r = int(row_data.get("returned") or 0)
            
            # Use the row-level strength when supplied, otherwise the class default.
            row_str = row_data.get("strength")
            if row_str is not None and str(row_str).strip().isdigit():
                row_strength = int(row_str)
            else:
                row_strength = default_class_strength
        except ValueError: 
            raise ValueError(f"Invalid numbers for book '{row_data.get('bookName', 'Unknown')}'.")
        
        if p < 0 or d < 0 or r < 0 or row_strength < 0: 
            raise ValueError(f"Stock quantities and strength cannot be negative for '{row_data.get('bookName')}'.")
        
        balance = calculate_balance(p, d, r)
        if balance < 0: 
            raise ValueError(f"Transaction rejected: Distributing {d} books when only {p+r} are available creates a negative balance for '{row_data.get('bookName')}'.")
        
        row_data["strength"] = str(row_strength)
        row_data["balance"] = str(balance)
        row_data["booksRequired"] = str(calculate_books_required(row_strength, p))
        row_data["modified_by"] = username
        row_data["modified_time"] = current_timestamp()

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
        record = {
            "id": f"L_{uuid.uuid4().hex[:8]}", 
            "school_id": str(school_id), 
            "class_id": str(class_id),
            "created_by": username, 
            "created_time": current_timestamp()
        }
        process_row(record, new_row)
        changed_books.append(f"added '{record.get('bookName', record['id'])}'")
        ledger.insert(0, record)
        
    atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, ledger)

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

