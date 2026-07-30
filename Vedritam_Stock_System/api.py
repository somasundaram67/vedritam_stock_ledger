# api.py
from fastapi.responses import StreamingResponse
import io
import csv
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

import database
import modules
import utils
from config import SIGNUP_ENABLED, SIGNUP_DEFAULT_ROLE, MIN_PASSWORD_LENGTH

router = APIRouter(prefix="/api/v1")
security = HTTPBearer()


# --- Identity helpers --------------------------------------------------------
class Identity:
    """Current identity for the caller, read fresh from users.csv on
    every request so that disabling an account or changing its role takes
    effect immediately, without waiting for the token to expire)."""

    def __init__(self, record: Dict[str, Any]):
        self.username = record.get("username", "")
        self.role = record.get("role", "")
        self.school_id = str(record.get("school_id", "") or "")
        self.status = record.get("status", "")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def assert_school(self, school_id) -> None:
        """School-scoped accounts may only touch their own school."""
        if self.is_admin or not self.school_id:
            return
        if str(school_id) != self.school_id:
            raise HTTPException(status_code=403, detail="You do not have access to this school.")


def get_identity(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Identity:
    payload = utils.decode_access_token(credentials.credentials)
    record = database.get_user_raw(payload.get("sub"))
    if not record:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    if record.get("status") != "Active":
        raise HTTPException(status_code=403, detail=f"Account is {record.get('status', 'inactive')}. Contact your administrator.")
    return Identity(record)


def get_current_user(identity: Identity = Depends(get_identity)) -> str:
    return identity.username


def require_admin(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return identity


# --- Schemas -----------------------------------------------------------------
class LoginRequest(BaseModel): username: str; password: str
class SyncRequest(BaseModel): schoolId: int; classId: int; updates: List[Dict[str, Any]]; deletes: List[str]
class SchoolCreate(BaseModel): name: str; code: str = ""; location: str = ""
class ClassCreate(BaseModel): name: str; strength: int
class StrengthUpdate(BaseModel):
    strength: int


class SignupRequest(BaseModel):
    username: str
    password: str
    fullName: str = ""
    email: str = ""
    schoolName: str = ""


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "school"
    fullName: str = ""
    email: str = ""
    school_id: str = ""
    status: str = "Active"


class UserUpdate(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None
    fullName: Optional[str] = None
    email: Optional[str] = None
    school_id: Optional[str] = None


class PasswordSet(BaseModel):
    newPassword: str


class PasswordChange(BaseModel):
    currentPassword: str
    newPassword: str


def _validate_password(pwd: str):
    if not pwd or len(pwd) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")


# --- Authentication ----------------------------------------------------------
@router.post("/auth/login")
def login(request: LoginRequest):
    record = database.get_user_raw(request.username)
    if not record:
        raise HTTPException(status_code=401, detail="User not found")
    if not utils.verify_password(request.password, record.get('password_hash', '')):
        database.log_action(record['username'], record.get('role', ''), "LOGIN_FAILED", "auth", record['username'], "Wrong password")
        raise HTTPException(status_code=401, detail="Wrong password")
    if record.get('status') == 'Pending':
        raise HTTPException(status_code=403, detail="Your account is awaiting administrator approval.")
    if record.get('status') == 'Disabled':
        raise HTTPException(status_code=403, detail="This account has been disabled. Contact your administrator.")

    user = database.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    database.update_user_login(user['username'])
    database.log_action(user['username'], user['role'], "LOGIN", "auth", user['username'], "Signed in")
    return {
        "access_token": utils.create_access_token({"sub": user["username"], "role": user["role"]}),
        "username": user["username"],
        "role": user["role"],
        "fullName": user.get("fullName", ""),
        "school_id": user.get("school_id", ""),
    }


@router.post("/auth/signup")
def signup(request: SignupRequest):
    """Public account request. The account is created as Pending — an
    administrator must approve it before the user can sign in."""
    if not SIGNUP_ENABLED:
        raise HTTPException(status_code=403, detail="Self sign-up is disabled. Ask your administrator for an account.")
    _validate_password(request.password)
    # Match the requested school name to an existing school when possible.
    school_id = ""
    requested = (request.schoolName or "").strip()
    if requested:
        match = next((s for s in database.get_all_schools() if s["name"].lower() == requested.lower()
                      or (s["code"] or "").lower() == requested.lower()), None)
        if match:
            school_id = str(match["id"])
    try:
        created = database.create_user(
            username=request.username,
            password=request.password,
            role=SIGNUP_DEFAULT_ROLE,
            full_name=request.fullName,
            email=request.email,
            school_id=school_id,
            status="Pending",
            created_by="self-signup",
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    database.log_action(created["username"], created["role"], "SIGNUP_REQUEST", "user", created["username"],
                        f"Requested access for school '{requested or 'unassigned'}'")
    return {"status": "pending",
            "message": "Account request submitted. An administrator must approve it before you can sign in."}


@router.get("/auth/me")
def me(identity: Identity = Depends(get_identity)):
    profile = database.get_user_profile(identity.username)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found.")
    return profile


@router.post("/auth/change-password")
def change_own_password(request: PasswordChange, identity: Identity = Depends(get_identity)):
    record = database.get_user_raw(identity.username)
    if not utils.verify_password(request.currentPassword, record.get('password_hash', '')):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    _validate_password(request.newPassword)
    database.set_password(identity.username, request.newPassword)
    database.log_action(identity.username, identity.role, "PASSWORD_CHANGE", "user", identity.username,
                        "Changed own password")
    return {"status": "success"}


# --- User Administration (admin only) ----------------------------------------
@router.get("/users")
def list_users(admin: Identity = Depends(require_admin)):
    return database.get_all_users()


@router.post("/users")
def create_user(request: UserCreate, admin: Identity = Depends(require_admin)):
    _validate_password(request.password)
    try:
        created = database.create_user(
            username=request.username,
            password=request.password,
            role=request.role,
            full_name=request.fullName,
            email=request.email,
            school_id=request.school_id,
            status=request.status,
            created_by=admin.username,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "USER_CREATE", "user", created["username"],
                        f"Created {created['role']} account (status {created['status']})")
    return {"status": "success", "data": created}


@router.put("/users/{username}")
def edit_user(username: str, request: UserUpdate, admin: Identity = Depends(require_admin)):
    try:
        updated = database.update_user(
            username,
            role=request.role,
            status=request.status,
            full_name=request.fullName,
            email=request.email,
            school_id=request.school_id,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    changes = ", ".join(f"{k}={v}" for k, v in request.dict(exclude_none=True).items()) or "no changes"
    database.log_action(admin.username, admin.role, "USER_UPDATE", "user", username, changes)
    return {"status": "success", "data": updated}


@router.post("/users/{username}/password")
def reset_user_password(username: str, request: PasswordSet, admin: Identity = Depends(require_admin)):
    _validate_password(request.newPassword)
    if not database.get_user_raw(username):
        raise HTTPException(status_code=404, detail="User not found.")
    database.set_password(username, request.newPassword)
    database.log_action(admin.username, admin.role, "PASSWORD_RESET", "user", username,
                        "Administrator reset the password")
    return {"status": "success"}


@router.delete("/users/{username}")
def remove_user(username: str, admin: Identity = Depends(require_admin)):
    if username.lower() == admin.username.lower():
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    try:
        database.delete_user(username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "USER_DELETE", "user", username, "Account removed")
    return {"status": "success"}


# --- Activity Log (admin only) ------------------------------------------------
@router.get("/audit")
def audit(limit: int = 200, username: str = "", action: str = "", admin: Identity = Depends(require_admin)):
    return database.get_audit_log(limit=limit, username=username, action=action)


@router.get("/audit/download")
def audit_download(admin: Identity = Depends(require_admin)):
    rows = database.get_audit_log(limit=2000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "User", "Role", "Action", "Entity", "Reference", "Details"])
    for r in rows:
        writer.writerow([r.get("timestamp", ""), r.get("username", ""), r.get("role", ""),
                         r.get("action", ""), r.get("entity", ""), r.get("entity_id", ""), r.get("details", "")])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="Vedritam_Activity_Log.csv"'})


# --- Dashboard ----------------------------------------------------------------
@router.get("/dashboard")
def dashboard(identity: Identity = Depends(get_identity)):
    """Live analytics computed from the real ledger, schools and audit data."""
    data = database.get_dashboard_stats(
        school_id_filter="" if identity.is_admin else identity.school_id
    )
    data["presence"] = database.presence_snapshot()
    data["mySchoolsAdded"] = database.schools_created_by(identity.username)
    data["me"] = {"username": identity.username, "role": identity.role, "school_id": identity.school_id}
    return data


# --- Schools ------------------------------------------------------------------
@router.get("/schools")
def get_schools(identity: Identity = Depends(get_identity)):
    # School-scoped accounts only ever see their own school.
    return database.get_all_schools(school_id_filter="" if identity.is_admin else identity.school_id)


@router.post("/schools")
def create_school(request: SchoolCreate, identity: Identity = Depends(get_identity)):
    # Any signed-in user (admin, staff, school) can register a school.
    try:
        created = database.add_school(request.name.strip(), request.code.strip(), request.location.strip())
        database.log_action(identity.username, identity.role, "SCHOOL_CREATE", "school", created["id"], f"Added '{created['name']}'")
        return {"status": "success", "data": created}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/schools/{school_id}")
def delete_school(school_id: int, admin: Identity = Depends(require_admin)):
    # Deletion is admin-only.
    try:
        database.delete_school(school_id)
        database.log_action(admin.username, admin.role, "SCHOOL_DELETE", "school", school_id, "School removed")
        return {"status": "success"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))



@router.get("/schools/{school_id}/classes")
def get_classes(school_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    return database.get_classes_for_school(school_id)


@router.post("/schools/{school_id}/classes")
def create_class(school_id: int, request: ClassCreate, admin: Identity = Depends(get_identity)):
    # A school account manages only its own classes; admins manage any.
    admin.assert_school(school_id)
    if request.strength < 0:
        raise HTTPException(status_code=400, detail="Class strength cannot be negative.")
    try:
        created = database.add_class_to_school(school_id, request.name.strip(), request.strength)
        database.log_action(admin.username, admin.role, "CLASS_CREATE", "class", f"{school_id}:{created['id']}",
                            f"Added class '{created['name']}' (strength {created['strength']})")
        return {"status": "success", "data": created}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/schools/{school_id}/classes/{class_id}")
def delete_class(school_id: int, class_id: int, admin: Identity = Depends(require_admin)):
    try:
        database.delete_class(school_id, class_id)
        database.log_action(admin.username, admin.role, "CLASS_DELETE", "class", f"{school_id}:{class_id}", "Class removed")
        return {"status": "success"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.put("/schools/{school_id}/classes/{class_id}/strength")
def update_strength(school_id: int, class_id: int, request: StrengthUpdate, admin: Identity = Depends(get_identity)):
    admin.assert_school(school_id)
    if request.strength < 0:
        raise HTTPException(status_code=400, detail="Class strength cannot be negative.")
    try:
        database.update_class_strength(school_id, class_id, request.strength)
        database.log_action(admin.username, admin.role, "CLASS_STRENGTH", "class", f"{school_id}:{class_id}",
                            f"Strength set to {request.strength}")
        return {"status": "success"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


# --- Ledger --------------------------------------------------------------------
@router.get("/ledger/{school_id}/{class_id}")
def get_ledger(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    return database.get_ledger_records(school_id, class_id)


@router.post("/ledger/sync")
def sync_ledger(request: SyncRequest, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    try:
        changes = database.sync_ledger_records(request.schoolId, request.classId, request.updates, request.deletes, identity.username)
        summary = "; ".join(changes[:12]) or "no effective change"
        if len(changes) > 12:
            summary += f" (+{len(changes) - 12} more)"
        database.log_action(identity.username, identity.role, "LEDGER_SYNC", "ledger",
                            f"{request.schoolId}:{request.classId}", summary)
        return {"status": "success", "synced": len(request.updates) + len(request.deletes)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ledger/{school_id}/{class_id}/download")
def download_ledger_csv(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    records = database.get_ledger_records(school_id, class_id)
    
    # Resolve school and class names for the export filename.
    school = next((s for s in database.get_all_schools() if str(s["id"]) == str(school_id)), {})
    school_name = school.get("name", f"School_{school_id}").replace(" ", "_")
    
    classes = database.get_classes_for_school(school_id)
    class_info = next((c for c in classes if str(c["id"]) == str(class_id)), {})
    class_name = class_info.get("name", f"Class_{class_id}").replace(" ", "_")
    strength = class_info.get("strength", 0)
    
    # Build the CSV in memory.
    output = io.StringIO()
    writer = csv.writer(output)
    
    headers = ["Book Name", "Subject", "Publication", "Vendor", "Category", "Invoice Ref", "Strength", "Purchased", "Req. Books", "Distributed", "Returned", "Balance", "Remarks"]
    writer.writerow(headers)
    
    for r in records:
        writer.writerow([
            r.get("bookName", ""), r.get("subject", ""), r.get("publication", ""), 
            r.get("vendor", ""), r.get("category", ""), r.get("invoiceRef", ""),
            strength, r.get("purchased", 0), r.get("booksRequired", 0),
            r.get("distributed", 0), r.get("returned", 0), r.get("balance", 0), r.get("remarks", "")
        ])
        
    output.seek(0)
    database.log_action(identity.username, identity.role, "LEDGER_EXPORT", "ledger", f"{school_id}:{class_id}",
                        f"Downloaded CSV for {class_name}")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Ledger_{school_name}_{class_name}.csv"'}
    )

# --- AI assistant: admin-managed key + permission-scoped chat ----------------
# Provider calls are proxied here so the API key stays on the server and the
# ledger context is limited to what the caller may see.
import json as _json
import urllib.request as _urlreq
import urllib.error as _urlerr


class AISettingsUpdate(BaseModel):
    apiKey: Optional[str] = ""      # blank = keep the stored key
    apiBase: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"


def _mask(key: str) -> str:
    if not key:
        return ""
    return (key[:3] + "..." + key[-4:]) if len(key) > 10 else "..."


@router.get("/settings/ai")
def get_ai_settings(identity: Identity = Depends(get_identity)):
    """Assistant status; provider details are admin-only and the key is never returned."""
    cfg = database.get_ai_settings()
    out = {"configured": bool(cfg["apiKey"]), "canEdit": identity.is_admin}
    if identity.is_admin:
        out.update({"apiBase": cfg["apiBase"], "model": cfg["model"], "maskedKey": _mask(cfg["apiKey"])})
    return out


@router.put("/settings/ai")
def update_ai_settings(request: AISettingsUpdate, admin: Identity = Depends(require_admin)):
    cfg = database.save_ai_settings(request.apiKey, request.apiBase, request.model)
    database.log_action(admin.username, admin.role, "AI_SETTINGS_UPDATE", "settings", "ai",
                        f"Model {cfg['model']} @ {cfg['apiBase']}")
    return {"status": "success", "configured": bool(cfg["apiKey"]),
            "apiBase": cfg["apiBase"], "model": cfg["model"], "maskedKey": _mask(cfg["apiKey"])}


@router.delete("/settings/ai")
def delete_ai_settings(admin: Identity = Depends(require_admin)):
    database.clear_ai_settings()
    database.log_action(admin.username, admin.role, "AI_SETTINGS_CLEAR", "settings", "ai", "Key removed")
    return {"status": "success", "configured": False}


@router.post("/settings/ai/test")
def test_ai_settings(admin: Identity = Depends(require_admin)):
    cfg = database.get_ai_settings()
    if not cfg["apiKey"]:
        raise HTTPException(status_code=400, detail="No API key saved yet.")
    req = _urlreq.Request(cfg["apiBase"].rstrip("/") + "/models",
                          headers={"Authorization": "Bearer " + cfg["apiKey"]})
    try:
        with _urlreq.urlopen(req, timeout=30):
            return {"status": "success", "detail": "Connection OK."}
    except _urlerr.HTTPError as he:
        raise HTTPException(status_code=400, detail=f"Provider rejected the key (HTTP {he.code}).")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach the provider: {e}")


class ChatRequest(BaseModel):
    question: str
    history: List[Dict[str, Any]] = []
    mode: Optional[str] = "ledger"   # 'ledger' or 'library'



def _ledger_context(identity: Identity) -> str:
    """Build the assistant context from the data this caller may access."""
    schools = database.get_all_schools(school_id_filter="" if identity.is_admin else identity.school_id)
    rows = []
    for s in schools:
        for c in database.get_classes_for_school(s["id"]):
            for r in database.get_ledger_records(s["id"], c["id"]):
                rows.append({
                    "school": s.get("name"), "class": c.get("name"),
                    "book": r.get("bookName"), "subject": r.get("subject"),
                    "purchased": r.get("purchased"), "distributed": r.get("distributed"),
                    "returned": r.get("returned"), "balance": r.get("balance"),
                    "required": r.get("booksRequired"),
                })
    return _json.dumps(rows)[:12000]


@router.post("/chat")
def chat(request: ChatRequest, identity: Identity = Depends(get_identity)):
    cfg = database.get_ai_settings()
    if not cfg["apiKey"]:
        raise HTTPException(status_code=400,
                            detail="The assistant is not configured yet. Ask an administrator to add an API key in Settings.")

    question = (request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")

    scope = ("You are talking to an ADMINISTRATOR: the data below covers every school."
             if identity.is_admin else
             "You are talking to a SCHOOL ACCOUNT: the data below is ONLY that user's own school. "
             "You have no information about any other school or account. If asked about another "
             "school, another user, accounts, passwords or system-wide totals, reply that they can "
             "only see their own school's data and suggest contacting an administrator.")

    mode = (request.mode or "ledger").lower()
    scope_school = "" if identity.is_admin else identity.school_id
    if mode == "library":
        data_ctx = modules.library_context_json(scope_school)
        subject = ("school library — catalog, members (name/class/section/UID) and loans. "
                   "Answer questions about who borrowed a book, overdue loans and due dates.")
    else:
        data_ctx = _ledger_context(identity)
        subject = "school stock ledger — books purchased, distributed, returned, balance per class."

    system = ("You are a helpful assistant for a " + subject + " You can answer ANY question the "
              "user asks — general knowledge, explanations, calculations, writing help, and more. "
              "When the user asks about their own school data, use the JSON DATA below (already "
              "filtered to what this user is permitted to see) — never invent records that are not "
              "in it. For everything else, answer freely and helpfully. Be concise.\n"
              + scope + "\n\nDATA:\n" + data_ctx)


    # Only plain user/assistant turns from the client are trusted; the system
    # prompt and data are always rebuilt here.
    history = [m for m in (request.history or [])
               if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")][-10:]
    messages = [{"role": "system", "content": system}] + \
               [{"role": m["role"], "content": str(m["content"])} for m in history] + \
               [{"role": "user", "content": question}]

    url = cfg["apiBase"].rstrip("/") + "/chat/completions"
    body = _json.dumps({"model": cfg["model"], "messages": messages, "temperature": 0.3}).encode("utf-8")
    req = _urlreq.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["apiKey"],
    })
    try:
        with _urlreq.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except _urlerr.HTTPError as he:
        detail = he.read().decode("utf-8", "replace")
        try:
            detail = _json.loads(detail).get("error", {}).get("message", detail)
        except Exception:
            pass
        raise HTTPException(status_code=he.code, detail=f"AI provider error: {detail}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach AI provider at {url}: {e}")
    try:
        answer = data["choices"][0]["message"]["content"]
    except Exception:
        raise HTTPException(status_code=502, detail="AI provider returned an unexpected response.")
    database.log_action(identity.username, identity.role, "AI_CHAT", "chat", "", question[:120])
    return {"answer": answer}


# --- Distribution -------------------------------------------------------------
class DistributionCreate(BaseModel):
    schoolId: int
    classId: int
    ledgerId: str
    recipient: str
    quantity: int
    remarks: str = ""


@router.get("/distributions")
def get_distributions(identity: Identity = Depends(get_identity)):
    return modules.list_distributions("" if identity.is_admin else identity.school_id)


@router.post("/distributions")
def post_distribution(request: DistributionCreate, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    try:
        rec = modules.create_distribution(
            request.schoolId, request.classId, request.ledgerId,
            request.recipient, request.quantity, request.remarks, identity.username
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "DISTRIBUTION_ISSUE",
                        "distribution", f"{request.schoolId}:{request.classId}:{request.ledgerId}",
                        f"Issued {request.quantity} of '{rec['book_name']}' to {rec['recipient']}")
    return {"status": "success", "data": rec}


# --- Transfers ----------------------------------------------------------------
class TransferCreate(BaseModel):
    fromSchoolId: int
    toSchoolId: int
    bookName: str
    quantity: int
    remarks: str = ""


class TransferStatusUpdate(BaseModel):
    status: str
    remarks: Optional[str] = ""


@router.get("/transfers")
def get_transfers(identity: Identity = Depends(get_identity)):
    return modules.list_transfers("" if identity.is_admin else identity.school_id)


@router.post("/transfers")
def post_transfer(request: TransferCreate, identity: Identity = Depends(get_identity)):
    # School accounts can only send from their own school.
    identity.assert_school(request.fromSchoolId)
    try:
        rec = modules.create_transfer(
            request.fromSchoolId, request.toSchoolId, request.bookName,
            request.quantity, request.remarks, identity.username
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "TRANSFER_REQUEST", "transfer", rec["id"],
                        f"{request.quantity} of '{request.bookName}' from S{request.fromSchoolId} -> S{request.toSchoolId}")
    return {"status": "success", "data": rec}


@router.put("/transfers/{transfer_id}/status")
def put_transfer_status(transfer_id: str, request: TransferStatusUpdate,
                        identity: Identity = Depends(get_identity)):
    """Admin can decide any transfer; a school account can approve/reject
    transfers that were requested FROM their school (i.e. they are the holder
    of the stock being requested)."""
    existing = modules.get_transfer(transfer_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Transfer not found.")
    if not identity.is_admin:
        if not identity.school_id or str(existing.get("to_school_id")) != str(identity.school_id):
            raise HTTPException(status_code=403, detail="Only the destination school or an administrator can decide this transfer.")
    if request.status == "Rejected" and not (request.remarks or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required to reject a transfer.")
    try:
        rec = modules.set_transfer_status(transfer_id, request.status, identity.username,
                                          (request.remarks or "").strip())
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "TRANSFER_" + request.status.upper(),
                        "transfer", transfer_id, f"Transfer marked {request.status}"
                        + (f": {request.remarks}" if request.remarks else ""))
    return {"status": "success", "data": rec}


# --- Presence (file-backed heartbeat) ----------------------------------------
@router.post("/presence/ping")
def presence_ping(identity: Identity = Depends(get_identity)):
    database.record_presence(identity.username)
    return {"status": "ok"}


# --- Delete a distribution (reverses stock) -----------------------------------
@router.delete("/distributions/{dist_id}")
def delete_distribution(dist_id: str, identity: Identity = Depends(get_identity)):
    existing = modules.get_distribution(dist_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Distribution not found.")
    if not identity.is_admin:
        identity.assert_school(existing.get("school_id"))
    try:
        modules.delete_distribution(dist_id, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "DISTRIBUTION_DELETE",
                        "distribution", dist_id, f"Reversed {existing.get('quantity')} of '{existing.get('book_name')}'")
    return {"status": "success"}



# --- Library: schools (independent from ledger schools) ---------------------
class LibrarySchoolCreate(BaseModel):
    name: str
    code: str = ""
    location: str = ""


@router.get("/library/schools")
def get_library_schools(identity: Identity = Depends(get_identity)):
    return modules.list_library_schools()


@router.post("/library/schools")
def post_library_school(request: LibrarySchoolCreate, identity: Identity = Depends(get_identity)):
    try:
        rec = modules.create_library_school(request.name, request.code, request.location, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "LIBRARY_SCHOOL_ADD",
                        "library_school", rec["id"], f"Added library school '{rec['name']}'")
    return {"status": "success", "data": rec}


@router.delete("/library/schools/{school_id}")
def del_library_school(school_id: int, admin: Identity = Depends(require_admin)):
    try:
        modules.delete_library_school(school_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "LIBRARY_SCHOOL_DELETE", "library_school", school_id, "Removed")
    return {"status": "success"}


# --- Library: catalog ---------------------------------------------------------
class CatalogCreate(BaseModel):
    schoolId: int
    accession: str = ""
    title: str
    author: str = ""
    publisher: str = ""
    category: str = ""
    copies: int = 1
    remarks: str = ""


@router.get("/library/catalog")
def get_catalog(identity: Identity = Depends(get_identity)):
    return modules.list_catalog("" if identity.is_admin else identity.school_id)


@router.post("/library/catalog")
def post_catalog(request: CatalogCreate, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    try:
        rec = modules.create_catalog(request.schoolId, request.dict(), identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "LIBRARY_BOOK_ADD",
                        "catalog", rec["id"], f"Added '{rec['title']}' x{rec['copies']}")
    return {"status": "success", "data": rec}


@router.delete("/library/catalog/{book_id}")
def del_catalog(book_id: str, admin: Identity = Depends(require_admin)):
    try:
        modules.delete_catalog(book_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "LIBRARY_BOOK_DELETE", "catalog", book_id, "Removed")
    return {"status": "success"}


# --- Library: members ---------------------------------------------------------
class MemberCreate(BaseModel):
    schoolId: int
    name: str
    className: str
    section: str
    uid: str


@router.get("/library/members")
def get_members(identity: Identity = Depends(get_identity)):
    return modules.list_members("" if identity.is_admin else identity.school_id)


@router.post("/library/members")
def post_member(request: MemberCreate, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    try:
        rec = modules.create_member(request.schoolId, {
            "name": request.name, "class_name": request.className,
            "section": request.section, "uid": request.uid,
        }, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "LIBRARY_MEMBER_ADD",
                        "member", rec["id"],
                        f"Registered {rec['name']} (UID {rec['uid']}, {rec['class_name']}-{rec['section']})")
    return {"status": "success", "data": rec}


@router.delete("/library/members/{member_id}")
def del_member(member_id: str, admin: Identity = Depends(require_admin)):
    try:
        modules.delete_member(member_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "LIBRARY_MEMBER_DELETE", "member", member_id, "Removed")
    return {"status": "success"}


# --- Library: loans -----------------------------------------------------------
class LoanCreate(BaseModel):
    schoolId: int
    catalogId: str
    memberId: str
    dueAt: str      # YYYY-MM-DD
    remarks: str = ""


@router.get("/library/loans")
def get_loans(identity: Identity = Depends(get_identity)):
    return modules.list_loans("" if identity.is_admin else identity.school_id)


@router.post("/library/loans")
def post_loan(request: LoanCreate, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    try:
        rec = modules.create_loan(request.schoolId, request.catalogId,
                                  request.memberId, request.dueAt,
                                  request.remarks, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "LIBRARY_LOAN_ISSUE",
                        "loan", rec["id"], f"Loan due {rec['due_at']}")
    return {"status": "success", "data": rec}


@router.post("/library/loans/{loan_id}/return")
def return_loan(loan_id: str, identity: Identity = Depends(get_identity)):
    try:
        rec = modules.return_loan(loan_id, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    identity.assert_school(rec.get("school_id"))
    database.log_action(identity.username, identity.role, "LIBRARY_LOAN_RETURN",
                        "loan", loan_id, "Book returned")
    return {"status": "success", "data": rec}


@router.get("/library/reminders")
def get_library_reminders(identity: Identity = Depends(get_identity)):
    return modules.library_reminders("" if identity.is_admin else identity.school_id)
