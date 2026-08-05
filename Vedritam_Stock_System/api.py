# api.py
from fastapi.responses import StreamingResponse
import io
import csv
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

import base64
import database
import modules
import reporting
import utils
import messaging
import security as security_mod
import cache
from config import (SIGNUP_ENABLED, SIGNUP_DEFAULT_ROLE, MIN_PASSWORD_LENGTH,
                    DEFAULT_PAGE_SIZE, TWOFA_ENFORCED_ROLES)

router = APIRouter(prefix="/api/v1")
security = HTTPBearer()


# --- Identity & RBAC ---------------------------------------------------------
# super_admin : full platform access, every school.
# staff       : the schools assigned to them (own school + assigned_staff lists).
# user        : one school, and only the records they created.
class Identity:
    """Current identity for the caller, read fresh from users.csv on
    every request so that disabling an account or changing its role takes
    effect immediately, without waiting for the token to expire)."""

    def __init__(self, record: Dict[str, Any]):
        self.username = record.get("username", "")
        self.role = database.normalize_role(record.get("role", ""))
        self.school_id = str(record.get("school_id", "") or "")
        self.status = record.get("status", "")

    @property
    def is_super_admin(self) -> bool:
        return self.role == database.SUPER_ADMIN

    @property
    def is_staff(self) -> bool:
        return self.role == database.STAFF

    @property
    def school_ids(self) -> Optional[List[str]]:
        """Schools this identity may read. None means every school.
        The stock workspace (schools, classes, ledger, distribution,
        transfers, reports) is shared by admin, staff and user alike; only
        the API key settings, the activity log and security are admin-only.
        A user pinned to one school still only sees that school."""
        if self.is_super_admin or self.is_staff:
            return None
        return [self.school_id] if self.school_id else None

    @property
    def scope_school_id(self) -> str:
        """Single-school filter for legacy call sites; blank when unscoped."""
        return "" if self.is_super_admin or self.is_staff else self.school_id

    @property
    def owner_filter(self) -> str:
        """The ledger is a shared register: every role reads the same rows."""
        return ""

    def assert_school(self, school_id) -> None:
        """Data isolation gate: raises unless this school is in scope."""
        allowed = self.school_ids
        if allowed is None:
            return
        if not school_id or str(school_id) not in {str(i) for i in allowed}:
            raise HTTPException(status_code=403, detail="You do not have access to this school.")

    def assert_can_manage_schools(self) -> None:
        """Schools and classes are common workspace data - any signed-in
        account may add or edit them; the activity log records who did it."""
        return None


def get_identity(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Identity:
    payload = utils.decode_access_token(credentials.credentials)
    record = database.get_user_raw(payload.get("sub"))
    if not record:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    if record.get("status") != "Active":
        raise HTTPException(status_code=403, detail=f"Account is {record.get('status', 'inactive')}. Contact your administrator.")
    username = record.get("username", "")
    # Idle session timeout: a token stays valid only while the account keeps
    # using it. Going quiet for longer than the idle window ends the session.
    if security_mod.session_expired(username):
        security_mod.end_session(username)
        security_mod.record_login(username, "SESSION_TIMEOUT", detail="Idle session expired")
        raise HTTPException(status_code=401, detail="Session timed out due to inactivity. Please sign in again.")
    security_mod.touch_session(username)
    return Identity(record)


def get_current_user(identity: Identity = Depends(get_identity)) -> str:
    return identity.username


def require_super_admin(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_super_admin:
        raise HTTPException(status_code=403, detail="Super Admin access required.")
    return identity


def require_staff(identity: Identity = Depends(get_identity)) -> Identity:
    """Staff or Super Admin: management actions inside a school."""
    if not (identity.is_super_admin or identity.is_staff):
        raise HTTPException(status_code=403, detail="Staff access required.")
    return identity


# --- Schemas -----------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str
    code: Optional[str] = None  # TOTP / backup code for 2FA accounts
class SyncRequest(BaseModel):
    schoolId: int
    classId: int = 0
    standard: str = ""
    updates: List[Dict[str, Any]] = []
    deletes: List[str] = []

class SchoolCreate(BaseModel):
    """The School entity of the multi-school platform."""
    name: str
    code: str = ""
    location: str = ""
    logo: str = ""
    address: str = ""
    contact: str = ""
    academic_year: str = ""
    status: str = "Active"
    assigned_staff: List[str] = []
    settings: Dict[str, Any] = {}


class SchoolUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    location: Optional[str] = None
    logo: Optional[str] = None
    address: Optional[str] = None
    contact: Optional[str] = None
    academic_year: Optional[str] = None
    status: Optional[str] = None
    assigned_staff: Optional[List[str]] = None
    settings: Optional[Dict[str, Any]] = None
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
    # Optional: administrators no longer type a password when creating an
    # account. When omitted the server generates a strong random one, which
    # the admin can replace later with "Reset Password".
    password: Optional[str] = None
    role: str = database.USER
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


def utils_generate_password() -> str:
    """Strong random password used when an admin creates an account without
    typing one. It is never displayed; use Reset Password to set a known one."""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(14))
        if (any(c.islower() for c in candidate) and any(c.isupper() for c in candidate)
                and any(c.isdigit() for c in candidate)):
            return candidate


def _validate_password(pwd: str, username: str = ""):
    ok, reason = security_mod.validate_password(pwd, username)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)


def _client_ip(request: Optional[Request]) -> str:
    if not request:
        return ""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


# --- Authentication ----------------------------------------------------------
@router.post("/auth/login")
def login(request: LoginRequest, http_request: Request = None):
    ip = _client_ip(http_request)
    agent = http_request.headers.get("user-agent", "") if http_request else ""

    locked = security_mod.check_lockout(request.username)
    if locked:
        security_mod.record_login(request.username, "LOCKED", ip, agent,
                                  "Too many failed attempts")
        raise HTTPException(status_code=429,
                            detail=f"Too many failed attempts. Try again in {locked // 60 + 1} minute(s).")

    record = database.get_user_raw(request.username)
    if not record:
        security_mod.register_failure(request.username)
        security_mod.record_login(request.username, "FAILED", ip, agent, "Unknown account")
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    if not utils.verify_password(request.password, record.get('password_hash', '')):
        left = security_mod.register_failure(request.username)
        security_mod.record_login(record['username'], "FAILED", ip, agent, "Wrong password")
        database.log_action(record['username'], record.get('role', ''), "LOGIN_FAILED", "auth", record['username'], "Wrong password")
        raise HTTPException(status_code=401,
                            detail="Invalid username or password." +
                                   (f" {left} attempt(s) left before lockout." if left <= 2 else ""))
    if record.get('status') == 'Pending':
        raise HTTPException(status_code=403, detail="Your account is awaiting administrator approval.")
    if record.get('status') == 'Disabled':
        raise HTTPException(status_code=403, detail="This account has been disabled. Contact your administrator.")

    user = database.get_user_by_username(request.username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # --- Two-factor for Super Admin -----------------------------------------
    # Always challenged when 2FA is enrolled, and additionally forced when the
    # account has just failed the password several times in a row (step-up).
    login_role = database.normalize_role(user["role"])
    enrolled = security_mod.twofa_required(user["username"])
    stepup = (login_role in TWOFA_ENFORCED_ROLES
              and security_mod.stepup_required(request.username))
    force_setup = False

    if enrolled or stepup:
        if enrolled:
            if not request.code:
                security_mod.record_login(
                    user['username'], "2FA_REQUIRED", ip, agent,
                    "Repeated failed passwords" if stepup else "")
                return {"status": "2fa_required",
                        "reason": "failed_attempts" if stepup else "enabled",
                        "message": ("Several sign-in attempts failed. Enter the 6-digit code "
                                    "from your authenticator app to continue.") if stepup else
                                   "Enter the 6-digit code from your authenticator app."}
            if not security_mod.verify_totp(user["username"], request.code):
                security_mod.register_failure(request.username)
                security_mod.record_login(user['username'], "2FA_FAILED", ip, agent, "Bad code")
                raise HTTPException(status_code=401, detail="That verification code is not valid.")
        else:
            # Not enrolled yet: let the sign-in through but force enrolment.
            force_setup = True
            security_mod.record_login(user['username'], "2FA_REQUIRED", ip, agent,
                                      "Repeated failed passwords - enrolment forced")

    security_mod.clear_failures(request.username)
    security_mod.touch_session(user["username"])
    security_mod.record_login(user['username'], "SUCCESS", ip, agent,
                              f"role={database.normalize_role(user['role'])}")
    database.update_user_login(user['username'])
    database.log_action(user['username'], user['role'], "LOGIN", "auth", user['username'], "Signed in")
    role = database.normalize_role(user["role"])
    return {
        "access_token": utils.create_access_token({"sub": user["username"], "role": role}),
        "username": user["username"],
        "role": role,
        "fullName": user.get("fullName", ""),
        "school_id": user.get("school_id", ""),
        "twofa_enabled": security_mod.twofa_required(user["username"]),
        "twofa_recommended": role in TWOFA_ENFORCED_ROLES and not security_mod.twofa_required(user["username"]),
        "twofa_setup_required": force_setup,
        "session_timeout_minutes": security_mod.get_idle_minutes(),
    }


@router.post("/auth/signup")
def signup(request: SignupRequest):
    """Public account request. The account is created as Pending — an
    administrator must approve it before the user can sign in."""
    if not SIGNUP_ENABLED:
        raise HTTPException(status_code=403, detail="Self sign-up is disabled. Ask your administrator for an account.")
    _validate_password(request.password, request.username)
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
    _validate_password(request.newPassword, identity.username)
    database.set_password(identity.username, request.newPassword)
    security_mod.record_login(identity.username, "PASSWORD_CHANGED", detail="Own password changed")
    database.log_action(identity.username, identity.role, "PASSWORD_CHANGE", "user", identity.username,
                        "Changed own password")
    return {"status": "success"}


# --- User Administration ------------------------------------------------------
# Super Admin manages every account; Staff may only view accounts inside the
# schools assigned to them.
@router.get("/users")
def list_users(identity: Identity = Depends(require_staff)):
    users = database.get_all_users()
    allowed = identity.school_ids
    if allowed is None:
        return users
    wanted = {str(i) for i in allowed}
    return [u for u in users if str(u.get("school_id", "")) in wanted]


@router.post("/users")
def create_user(request: UserCreate, admin: Identity = Depends(require_super_admin)):
    password = (request.password or "").strip() or utils_generate_password()
    _validate_password(password, request.username)
    try:
        created = database.create_user(
            username=request.username,
            password=password,
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
def edit_user(username: str, request: UserUpdate, admin: Identity = Depends(require_super_admin)):
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
def reset_user_password(username: str, request: PasswordSet, admin: Identity = Depends(require_staff)):
    """Super Admin can reset any password. Staff may reset passwords for the
    ordinary user accounts they manage, but not for other admins or staff."""
    _validate_password(request.newPassword, username)
    target = database.get_user_raw(username)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if not admin.is_super_admin:
        target_role = str(target.get("role", "")).lower()
        if target_role in ("super_admin", "staff") and username.lower() != admin.username.lower():
            raise HTTPException(
                status_code=403,
                detail="Only a Super Admin can reset an administrator or staff password.")
    database.set_password(username, request.newPassword)
    database.log_action(admin.username, admin.role, "PASSWORD_RESET", "user", username,
                        "Administrator reset the password")
    return {"status": "success"}


@router.delete("/users/{username}")
def remove_user(username: str, admin: Identity = Depends(require_super_admin)):
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
def audit(limit: int = 200, offset: int = 0, page: int = 0, username: str = "",
          action: str = "", admin: Identity = Depends(require_super_admin)):
    """Paginated when `page`/`offset` is supplied; plain list otherwise so the
    existing Activity Log screen keeps working unchanged."""
    if page or offset:
        if page:
            offset = (max(1, page) - 1) * (limit or DEFAULT_PAGE_SIZE)
        return database.get_audit_log_page(limit=limit or DEFAULT_PAGE_SIZE,
                                           offset=offset, username=username, action=action)
    return database.get_audit_log(limit=limit, username=username, action=action)


@router.get("/audit/download")
def audit_download(admin: Identity = Depends(require_super_admin)):
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
def dashboard(school_id: str = "", date_from: str = "", date_to: str = "",
              staff: str = "", user: str = "", identity: Identity = Depends(get_identity)):
    """Live analytics computed from the real ledger, schools and audit data.
    The same aggregation powers /reports, so cards, charts and exports always
    show identical numbers."""
    data = reporting.build_report(
        school_id=school_id, date_from=date_from, date_to=date_to,
        staff=staff, user=user,
        scope_school_id=identity.scope_school_id,
    )
    data["presence"] = database.presence_snapshot()
    data["mySchoolsAdded"] = database.schools_created_by(identity.username)
    data["me"] = {"username": identity.username, "role": identity.role, "school_id": identity.school_id}
    return data


# --- Schools ------------------------------------------------------------------
@router.get("/schools")
def get_schools(identity: Identity = Depends(get_identity)):
    """Data isolation: each role only ever receives the schools in its scope."""
    return database.get_all_schools(school_id_filter=identity.scope_school_id,
                                    allowed_ids=identity.school_ids)


@router.get("/schools/{school_id}")
def get_school_detail(school_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    school = database.get_school(school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found.")
    return school


@router.post("/schools")
def create_school(request: SchoolCreate, identity: Identity = Depends(get_identity)):
    # Onboarding a school onto the platform is a Super Admin action.
    identity.assert_can_manage_schools()
    try:
        created = database.add_school(
            name=request.name, code=request.code, location=request.location,
            logo=request.logo, address=request.address, contact=request.contact,
            academic_year=request.academic_year, status=request.status,
            assigned_staff=request.assigned_staff, settings=request.settings,
        )
        database.log_action(identity.username, identity.role, "SCHOOL_CREATE", "school", created["id"], f"Added '{created['name']}'")
        return {"status": "success", "data": created}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.put("/schools/{school_id}")
def edit_school(school_id: int, request: SchoolUpdate, identity: Identity = Depends(get_identity)):
    identity.assert_can_manage_schools()
    try:
        updated = database.update_school(school_id, **request.model_dump(exclude_unset=True))
        database.log_action(identity.username, identity.role, "SCHOOL_UPDATE", "school", school_id,
                            f"Updated '{updated['name']}'")
        return {"status": "success", "data": updated}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/schools/{school_id}")
def delete_school(school_id: int, admin: Identity = Depends(get_identity)):
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
def delete_class(school_id: int, class_id: int, admin: Identity = Depends(get_identity)):
    admin.assert_school(school_id)
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


# --- Catalog (master book / notebook list) --------------------------------------
@router.get("/catalog/standards")
def catalog_standards(identity: Identity = Depends(get_identity)):
    """Standards for the ledger dropdown, plus the article categories."""
    return {
        "standards": database.get_catalog_standards(),
        "categories": database.get_article_categories(),
        "publications": database.get_catalog_publications(),
    }


@router.get("/catalog")
def catalog(standard: str = "ALL", category: str = "ALL",
            identity: Identity = Depends(get_identity)):
    """Titles available for a standard. standard=ALL returns every standard."""
    return database.get_catalog(standard, category)


@router.get("/vendors")
def vendors(identity: Identity = Depends(get_identity)):
    return database.get_vendors()


# --- Ledger --------------------------------------------------------------------
@router.get("/ledger/standard/{school_id}/{standard}")
def get_ledger_for_standard(school_id: int, standard: str, identity: Identity = Depends(get_identity)):
    """Ledger rows for one standard, or every standard when standard is 'ALL'."""
    identity.assert_school(school_id)
    return {
        "standard": standard.upper(),
        "strength": database.standard_strength(school_id, standard) if standard.upper() != "ALL" else 0,
        "rows": database.get_ledger_by_standard(school_id, standard, created_by=identity.owner_filter),
    }


@router.get("/ledger/{school_id}/{class_id}")
def get_ledger(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    return database.get_ledger_records(school_id, class_id, created_by=identity.owner_filter)


@router.post("/ledger/sync")
def sync_ledger(request: SyncRequest, identity: Identity = Depends(get_identity)):
    identity.assert_school(request.schoolId)
    if identity.owner_filter and request.deletes:
        foreign = database.rows_not_owned_by(request.deletes, identity.username)
        if foreign:
            raise HTTPException(status_code=403, detail="You can only modify records you created.")
    try:
        changes = database.sync_ledger_records(request.schoolId, request.classId, request.updates,
                                               request.deletes, identity.username,
                                               standard=request.standard)
        summary = "; ".join(changes[:12]) or "no effective change"
        if len(changes) > 12:
            summary += f" (+{len(changes) - 12} more)"
        database.log_action(identity.username, identity.role, "LEDGER_SYNC", "ledger",
                            f"{request.schoolId}:{request.standard or request.classId}", summary)
        return {"status": "success", "synced": len(request.updates) + len(request.deletes)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Full 22-column stock-register export, matching the printed register layout.
REGISTER_EXPORT_COLUMNS = [
    ("S.No", None),
    ("Standard", "standard"),
    ("Vendor ID", "vendorId"),
    ("Vendor Name", "vendor"),
    ("Contact Number", "vendorContact"),
    ("Vendor GST No", "vendorGst"),
    ("Invoice Date", "invoiceDate"),
    ("Invoice No", "invoiceRef"),
    ("Category", "category"),
    ("Subject", "subject"),
    ("Book / Article Name", "bookName"),
    ("Publication", "publication"),
    ("Edition / Year", "edition"),
    ("Opening Balance", "openingBalance"),
    ("Qty Purchased", "purchased"),
    ("Approved Rate", "approvedRate"),
    ("Base Rate", "baseRate"),
    ("GST Amount", "gstAmount"),
    ("Discount %", "discountPercent"),
    ("Discount Amount", "discountAmount"),
    ("Total Amount", "totalAmount"),
    ("Strength", "strength"),
    ("Req. Books", "booksRequired"),
    ("Issued / Distributed", "distributed"),
    ("Returns", "returned"),
    ("Closing Balance", "closingBalance"),
    ("Remarks", "remarks"),
]


def _register_csv(records, title: str) -> io.StringIO:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"STOCK REGISTER FOR TEXT BOOKS / NOTE BOOKS - {title}"])
    writer.writerow([h for h, _ in REGISTER_EXPORT_COLUMNS])
    for i, r in enumerate(records, start=1):
        writer.writerow([i if key is None else r.get(key, "") for _, key in REGISTER_EXPORT_COLUMNS])
    output.seek(0)
    return output


@router.get("/ledger/standard/{school_id}/{standard}/download")
def download_ledger_standard_csv(school_id: int, standard: str, identity: Identity = Depends(get_identity)):
    """Register export for one standard, or all standards when standard is 'ALL'."""
    identity.assert_school(school_id)
    records = database.get_ledger_by_standard(school_id, standard, created_by=identity.owner_filter)
    school = next((s for s in database.get_all_schools() if str(s["id"]) == str(school_id)), {})
    school_name = school.get("name", f"School_{school_id}").replace(" ", "_")
    label = "All_Standards" if standard.upper() == "ALL" else standard.upper().replace(" ", "_")
    output = _register_csv(records, label.replace("_", " "))
    database.log_action(identity.username, identity.role, "LEDGER_EXPORT", "ledger",
                        f"{school_id}:{standard}", f"Downloaded register for {label}")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Register_{school_name}_{label}.csv"'}
    )


@router.get("/ledger/{school_id}/{class_id}/download")
def download_ledger_csv(school_id: int, class_id: int, identity: Identity = Depends(get_identity)):
    identity.assert_school(school_id)
    records = database.get_ledger_records(school_id, class_id, created_by=identity.owner_filter)

    school = next((s for s in database.get_all_schools() if str(s["id"]) == str(school_id)), {})
    school_name = school.get("name", f"School_{school_id}").replace(" ", "_")

    classes = database.get_classes_for_school(school_id)
    class_info = next((c for c in classes if str(c["id"]) == str(class_id)), {})
    class_name = class_info.get("name", f"Class_{class_id}").replace(" ", "_")

    output = _register_csv(records, class_name.replace("_", " "))
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
    out = {"configured": bool(cfg["apiKey"]), "canEdit": identity.is_super_admin}
    if identity.is_super_admin:
        out.update({"apiBase": cfg["apiBase"], "model": cfg["model"], "maskedKey": _mask(cfg["apiKey"])})
    return out


@router.put("/settings/ai")
def update_ai_settings(request: AISettingsUpdate, admin: Identity = Depends(require_super_admin)):
    cfg = database.save_ai_settings(request.apiKey, request.apiBase, request.model)
    database.log_action(admin.username, admin.role, "AI_SETTINGS_UPDATE", "settings", "ai",
                        f"Model {cfg['model']} @ {cfg['apiBase']}")
    return {"status": "success", "configured": bool(cfg["apiKey"]),
            "apiBase": cfg["apiBase"], "model": cfg["model"], "maskedKey": _mask(cfg["apiKey"])}


@router.delete("/settings/ai")
def delete_ai_settings(admin: Identity = Depends(require_super_admin)):
    database.clear_ai_settings()
    database.log_action(admin.username, admin.role, "AI_SETTINGS_CLEAR", "settings", "ai", "Key removed")
    return {"status": "success", "configured": False}


@router.post("/settings/ai/test")
def test_ai_settings(admin: Identity = Depends(require_super_admin)):
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



def _ledger_context(identity: Identity) -> str:
    """Build the assistant context from the data this caller may access."""
    schools = database.get_all_schools(school_id_filter=identity.scope_school_id,
                                       allowed_ids=identity.school_ids)
    rows = []
    for s in schools:
        for c in database.get_classes_for_school(s["id"]):
            for r in database.get_ledger_records(s["id"], c["id"], created_by=identity.owner_filter):
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

    scope = ("You are talking to a SUPER ADMIN: the data below covers every school."
             if identity.is_super_admin else
             "You are talking to a SCOPED ACCOUNT: the data below is ONLY the school(s) this user "
             "is assigned to. "
             "You have no information about any other school or account. If asked about another "
             "school, another user, accounts, passwords or system-wide totals, reply that they can "
             "only see their own school's data and suggest contacting an administrator.")

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
    return modules.list_distributions(identity.scope_school_id,
                                      allowed_ids=identity.school_ids,
                                      created_by=identity.owner_filter)


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


def _can_decide_transfer(identity: "Identity", record: Dict[str, Any]) -> bool:
    """Who may approve / decline a transfer request.
    Super Admin always can. Any other account can when the destination school
    is inside its scope; an account with an unrestricted scope (school_ids is
    None) counts as in scope for every school."""
    if identity.is_super_admin:
        return True
    allowed = identity.school_ids
    if allowed is None:
        return True
    return str(record.get("to_school_id")) in {str(i) for i in allowed}


@router.get("/transfers")
def get_transfers(identity: Identity = Depends(get_identity)):
    rows = modules.list_transfers(identity.scope_school_id,
                                  allowed_ids=identity.school_ids,
                                  created_by=identity.owner_filter)
    for row in rows:
        row["can_decide"] = _can_decide_transfer(identity, row)
    return rows


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
    """A Super Admin can decide any transfer; otherwise the caller must be in
    scope for the destination school holding the stock being requested."""
    existing = modules.get_transfer(transfer_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Transfer not found.")
    if not _can_decide_transfer(identity, existing):
        raise HTTPException(status_code=403, detail="Only the receiving school or a Super Admin can decide this transfer.")
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
    if not identity.is_super_admin and existing.get("created_by") != identity.username:
        identity.assert_school(existing.get("school_id"))
    try:
        modules.delete_distribution(dist_id, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "DISTRIBUTION_DELETE",
                        "distribution", dist_id, f"Reversed {existing.get('quantity')} of '{existing.get('book_name')}'")
    return {"status": "success"}



# --- Reports & Analytics ------------------------------------------------------
@router.get("/reports")
def reports(school_id: str = "", date_from: str = "", date_to: str = "",
            staff: str = "", user: str = "", identity: Identity = Depends(get_identity)):
    """Full report payload: totals, charts, per-school rollups, record rows,
    pending tasks and activity - all from one validated aggregation."""
    return reporting.build_report(
        school_id=school_id, date_from=date_from, date_to=date_to,
        staff=staff, user=user,
        scope_school_id=identity.scope_school_id,
    )


@router.get("/reports/export")
def reports_export(school_id: str = "", date_from: str = "", date_to: str = "",
                   staff: str = "", user: str = "", identity: Identity = Depends(get_identity)):
    """CSV/Excel export of exactly the rows the dashboard is showing."""
    data = reporting.build_report(
        school_id=school_id, date_from=date_from, date_to=date_to,
        staff=staff, user=user,
        scope_school_id=identity.scope_school_id,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for _, label in reporting.EXPORT_COLUMNS])
    for row in data["records"]:
        writer.writerow([row.get(key, "") for key, _ in reporting.EXPORT_COLUMNS])
    t = data["totals"]
    writer.writerow([])
    writer.writerow(["TOTALS", "", "", "", "", "", "", "", t["students"],
                     t["purchased"], t["distributed"], t["returned"], t["balance"],
                     t["required"], "", "", "", "", ""])
    writer.writerow([])
    writer.writerow(["Generated", data["generated_at"]])
    writer.writerow(["Schools", t["schools"], "Classes", t["classes"], "Records", t["records"],
                     "Users", t["users"], "Staff", t["staff"]])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="Vedritam_Report.csv"'})


# =============================================================================
# MESSAGING
# Direct messages, school groups, admin announcements, attachments,
# read receipts, typing indicators and search.
# =============================================================================
class ConversationCreate(BaseModel):
    type: str = "dm"                 # dm | group | announcement
    title: str = ""
    members: List[str] = []
    school_id: str = ""


class MembersAdd(BaseModel):
    members: List[str]


class MessageSend(BaseModel):
    body: str = ""
    attachment: Optional[Dict[str, Any]] = None   # {name, type, data(base64)}


class AnnouncementCreate(BaseModel):
    title: str
    body: str
    school_id: str = ""              # blank = every school


class PublicKeyUpload(BaseModel):
    public_jwk: str


def _assert_member(conversation: Dict[str, Any], identity: Identity) -> None:
    if conversation.get("type") == messaging.ANNOUNCEMENT:
        return
    # Private chats are end-to-end encrypted and strictly need-to-know: there
    # is deliberately no Super Admin override here.
    members = {m.lower() for m in conversation.get("members", [])}
    if identity.username.lower() not in members:
        raise HTTPException(status_code=403, detail="You are not part of this conversation.")


@router.get("/messaging/conversations")
def messaging_conversations(identity: Identity = Depends(get_identity)):
    return {"data": messaging.list_conversations(identity.username)}


@router.post("/messaging/conversations")
def messaging_create_conversation(request: ConversationCreate,
                                  identity: Identity = Depends(get_identity)):
    ctype = (request.type or "dm").lower()
    if ctype == messaging.ANNOUNCEMENT:
        if not identity.is_super_admin:
            raise HTTPException(status_code=403, detail="Only a Super Admin can post announcements.")
        # There is only ever one announcement profile.
        return {"status": "success", "data": messaging.announcement_channel(identity.username)}
    if ctype == messaging.GROUP and not (identity.is_super_admin or identity.is_staff):
        raise HTTPException(status_code=403, detail="Only Staff or a Super Admin can create school groups.")
    # Members must be real, active accounts.
    known = {u["username"].lower(): u["username"] for u in database.get_all_users()}
    members = []
    for m in request.members:
        if m.lower() not in known:
            raise HTTPException(status_code=400, detail=f"Unknown user '{m}'.")
        members.append(known[m.lower()])
    try:
        conv = messaging.create_conversation(ctype, request.title.strip(), members,
                                             identity.username, request.school_id)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(identity.username, identity.role, "CONVERSATION_CREATE",
                        "conversation", conv["id"], f"{ctype}: {conv.get('title','')}")
    return {"status": "success", "data": conv}


@router.post("/messaging/conversations/{conversation_id}/members")
def messaging_add_members(conversation_id: str, request: MembersAdd,
                          identity: Identity = Depends(require_staff)):
    try:
        return {"status": "success", "data": messaging.add_members(conversation_id, request.members)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/messaging/conversations/{conversation_id}/messages")
def messaging_messages(conversation_id: str, limit: int = 50, offset: int = 0,
                       q: str = "", identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    page = messaging.list_messages(conversation_id, limit=limit, offset=offset, query=q)
    page["conversation"] = conv
    page["typing"] = messaging.who_is_typing(conversation_id, exclude=identity.username)
    return page


@router.post("/messaging/conversations/{conversation_id}/messages")
def messaging_send(conversation_id: str, request: MessageSend,
                   identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    if conv.get("type") == messaging.ANNOUNCEMENT and not identity.is_super_admin:
        raise HTTPException(status_code=403, detail="Announcements are read-only.")

    attachment = None
    if request.attachment:
        raw = request.attachment.get("data", "")
        if "," in raw[:64]:
            raw = raw.split(",", 1)[1]     # strip data: URL prefix
        try:
            blob = base64.b64decode(raw, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Attachment could not be read.")
        try:
            attachment = messaging.save_attachment(
                request.attachment.get("name", "attachment"),
                request.attachment.get("type", ""), blob)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

    try:
        msg = messaging.send_message(conversation_id, identity.username, request.body, attachment)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {"status": "success", "data": msg}


@router.delete("/messaging/messages/{message_id}")
def messaging_delete(message_id: str, identity: Identity = Depends(get_identity)):
    try:
        messaging.delete_message(message_id, identity.username, identity.is_super_admin)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return {"status": "success"}


@router.post("/messaging/conversations/{conversation_id}/read")
def messaging_mark_read(conversation_id: str, identity: Identity = Depends(get_identity)):
    conv = messaging.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    _assert_member(conv, identity)
    return messaging.mark_read(conversation_id, identity.username)


@router.post("/messaging/conversations/{conversation_id}/typing")
def messaging_typing(conversation_id: str, identity: Identity = Depends(get_identity)):
    messaging.set_typing(conversation_id, identity.username)
    return {"status": "ok", "typing": messaging.who_is_typing(conversation_id, identity.username)}


@router.get("/messaging/conversations/{conversation_id}/typing")
def messaging_typing_state(conversation_id: str, identity: Identity = Depends(get_identity)):
    return {"typing": messaging.who_is_typing(conversation_id, identity.username)}


@router.get("/messaging/search")
def messaging_search(q: str = "", limit: int = 50, identity: Identity = Depends(get_identity)):
    return {"data": messaging.search_messages(identity.username, q, limit)}


@router.get("/messaging/unread")
def messaging_unread(identity: Identity = Depends(get_identity)):
    counts = messaging.unread_counts(identity.username)
    return {"total": sum(counts.values()), "by_conversation": counts}


@router.get("/messaging/attachments/{attachment_id}")
def messaging_attachment(attachment_id: str, identity: Identity = Depends(get_identity)):
    path = messaging.attachment_path(attachment_id)
    if not path:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    owner_msg = messaging.message_for_attachment(attachment_id)
    if not owner_msg:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    conv = messaging.get_conversation(owner_msg.get("conversation_id", ""))
    if not conv:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    _assert_member(conv, identity)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=owner_msg.get("attachment_name") or "attachment")


@router.post("/messaging/announcements")
def messaging_announce(request: AnnouncementCreate,
                       admin: Identity = Depends(require_super_admin)):
    """Post into the single Announcements profile + notify every recipient."""
    users = database.get_all_users()
    if request.school_id:
        users = [u for u in users if str(u.get("school_id", "")) == str(request.school_id)]
    recipients = [u["username"] for u in users]
    conv = messaging.announcement_channel(admin.username)
    title = request.title.strip()
    body = (title + "\n" + request.body.strip()) if title else request.body.strip()
    messaging.send_message(conv["id"], admin.username, body)
    for member in recipients:
        if member.lower() == admin.username.lower():
            continue
        messaging.push_notification(member, "announcement", title or "Announcement",
                                    request.body.strip()[:160],
                                    "messages.html?c=" + str(conv["id"]))
    database.log_action(admin.username, admin.role, "ANNOUNCEMENT", "conversation",
                        conv["id"], title[:120])
    return {"status": "success", "data": conv, "recipients": len(recipients)}


# =============================================================================
# END-TO-END ENCRYPTION KEY DIRECTORY
# The server only ever stores public keys. Private keys never leave the
# browser, so message bodies are unreadable to the server and to admins.
# =============================================================================
@router.post("/messaging/keys")
def messaging_publish_key(request: PublicKeyUpload,
                          identity: Identity = Depends(get_identity)):
    if len(request.public_jwk) > 4000:
        raise HTTPException(status_code=400, detail="Key material is too large.")
    return {"status": "success",
            "data": messaging.save_public_key(identity.username, request.public_jwk)}


@router.get("/messaging/keys")
def messaging_get_keys(users: str = "", identity: Identity = Depends(get_identity)):
    names = [u.strip() for u in (users or "").split(",") if u.strip()]
    return {"data": messaging.get_public_keys(names)}


# =============================================================================
# NOTIFICATION CENTER
# Messages, reports, announcements and alerts, with mark-as-read and history.
# =============================================================================
@router.get("/notifications")
def notifications_list(unread: bool = False, type: str = "", limit: int = 30,
                       offset: int = 0, identity: Identity = Depends(get_identity)):
    return messaging.list_notifications(identity.username, unread_only=unread,
                                        ntype=type, limit=limit, offset=offset)


@router.post("/notifications/{notification_id}/read")
def notifications_read(notification_id: str, identity: Identity = Depends(get_identity)):
    try:
        messaging.mark_notification_read(notification_id, identity.username)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    return {"status": "success"}


@router.post("/notifications/read-all")
def notifications_read_all(identity: Identity = Depends(get_identity)):
    return {"status": "success", "marked": messaging.mark_all_notifications_read(identity.username)}


class NotificationPush(BaseModel):
    usernames: List[str] = []
    type: str = "alert"
    title: str
    body: str = ""
    link: str = ""


@router.post("/notifications/push")
def notifications_push(request: NotificationPush,
                       admin: Identity = Depends(require_super_admin)):
    targets = request.usernames or [u["username"] for u in database.get_all_users()]
    sent = messaging.broadcast_notification(targets, request.type, request.title,
                                            request.body, request.link)
    return {"status": "success", "sent": sent}


@router.get("/notifications/history")
def notifications_history(limit: int = 100, offset: int = 0,
                          identity: Identity = Depends(get_identity)):
    return messaging.list_notifications(identity.username, limit=limit, offset=offset)


# =============================================================================
# SECURITY
# Login history, session state, password strength and Super Admin 2FA.
# =============================================================================
class TwoFactorCode(BaseModel):
    code: str


class PasswordCheck(BaseModel):
    password: str


@router.get("/security/login-history")
def security_login_history(username: str = "", result: str = "", limit: int = 100,
                           offset: int = 0, identity: Identity = Depends(get_identity)):
    """Super Admin sees everyone; everyone else sees only their own history."""
    if not identity.is_super_admin:
        username = identity.username
    return security_mod.login_history(username=username, result=result,
                                      limit=limit, offset=offset)


@router.get("/security/session")
def security_session(identity: Identity = Depends(get_identity)):
    return security_mod.session_info(identity.username)


class SessionTimeoutUpdate(BaseModel):
    minutes: int


@router.put("/security/session/timeout")
def security_session_timeout(request: SessionTimeoutUpdate,
                             admin: Identity = Depends(require_super_admin)):
    """Edit the idle session timeout (Security page)."""
    try:
        minutes = security_mod.set_idle_minutes(request.minutes)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    database.log_action(admin.username, admin.role, "SESSION_TIMEOUT_UPDATE", "security",
                        admin.username, f"Idle timeout set to {minutes} minutes")
    return security_mod.session_info(admin.username)


@router.post("/security/session/heartbeat")
def security_session_heartbeat(identity: Identity = Depends(get_identity)):
    security_mod.touch_session(identity.username)
    return security_mod.session_info(identity.username)


@router.post("/security/logout")
def security_logout(identity: Identity = Depends(get_identity)):
    security_mod.end_session(identity.username)
    security_mod.record_login(identity.username, "LOGOUT")
    database.log_action(identity.username, identity.role, "LOGOUT", "auth",
                        identity.username, "Signed out")
    return {"status": "success"}


@router.post("/security/password/strength")
def security_password_strength(request: PasswordCheck,
                               identity: Identity = Depends(get_identity)):
    ok, reason = security_mod.validate_password(request.password, identity.username)
    return {"valid": ok, "reason": reason, **security_mod.password_strength(request.password)}


@router.get("/security/2fa")
def security_2fa_status(identity: Identity = Depends(get_identity)):
    status_payload = security_mod.twofa_status(identity.username)
    status_payload["required_for_role"] = identity.role in TWOFA_ENFORCED_ROLES
    return status_payload


@router.post("/security/2fa/enroll")
def security_2fa_enroll(admin: Identity = Depends(require_super_admin)):
    """Starts TOTP enrolment. The secret and backup codes are shown once."""
    data = security_mod.start_twofa_enrollment(admin.username)
    database.log_action(admin.username, admin.role, "2FA_ENROLL_START", "auth",
                        admin.username, "Started two-factor enrolment")
    return {"status": "pending", **data}


@router.post("/security/2fa/confirm")
def security_2fa_confirm(request: TwoFactorCode,
                         admin: Identity = Depends(require_super_admin)):
    if not security_mod.confirm_twofa(admin.username, request.code):
        raise HTTPException(status_code=400, detail="That verification code is not valid.")
    database.log_action(admin.username, admin.role, "2FA_ENABLED", "auth",
                        admin.username, "Two-factor authentication enabled")
    security_mod.record_login(admin.username, "2FA_ENABLED")
    return {"status": "success", "enabled": True}


@router.post("/security/2fa/disable")
def security_2fa_disable(request: TwoFactorCode,
                         admin: Identity = Depends(require_super_admin)):
    if not security_mod.verify_totp(admin.username, request.code):
        raise HTTPException(status_code=400, detail="Confirm with a valid code before disabling 2FA.")
    security_mod.disable_twofa(admin.username)
    database.log_action(admin.username, admin.role, "2FA_DISABLED", "auth",
                        admin.username, "Two-factor authentication disabled")
    return {"status": "success", "enabled": False}


# =============================================================================
# PERFORMANCE
# =============================================================================
@router.get("/system/cache")
def system_cache_stats(admin: Identity = Depends(require_super_admin)):
    return cache.stats()


@router.post("/system/cache/clear")
def system_cache_clear(admin: Identity = Depends(require_super_admin)):
    cache.clear()
    return {"status": "success"}
