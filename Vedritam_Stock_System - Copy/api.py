# api.py
from fastapi.responses import StreamingResponse
import io
import csv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Any

import database
import utils

router = APIRouter(prefix="/api/v1")
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    payload = utils.decode_access_token(credentials.credentials)
    return payload.get("sub")

def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    payload = utils.decode_access_token(credentials.credentials)
    if payload.get("role") != "admin": raise HTTPException(status_code=403, detail="Administrator access required.")
    return payload.get("sub")

class LoginRequest(BaseModel): username: str; password: str
class SyncRequest(BaseModel): schoolId: int; classId: int; updates: List[Dict[str, Any]]; deletes: List[str]
class SchoolCreate(BaseModel): name: str; code: str = ""; location: str = ""
class ClassCreate(BaseModel): name: str; strength: int
class StrengthUpdate(BaseModel): 
    strength: int

@router.post("/auth/login")
def login(request: LoginRequest):
    user = database.get_user_by_username(request.username)
    if not user or not utils.verify_password(request.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    database.update_user_login(user['username'])
    return {"access_token": utils.create_access_token({"sub": user["username"], "role": user["role"]}), "username": user["username"], "role": user["role"]}

@router.get("/schools")
def get_schools(user: str = Depends(get_current_user)): return database.get_all_schools()

@router.post("/schools")
def create_school(request: SchoolCreate, admin: str = Depends(require_admin)):
    try: return {"status": "success", "data": database.add_school(request.name.strip(), request.code.strip(), request.location.strip())}
    except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))

@router.delete("/schools/{school_id}")
def delete_school(school_id: int, admin: str = Depends(require_admin)):
    try:
        database.delete_school(school_id)
        return {"status": "success"}
    except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))

@router.get("/schools/{school_id}/classes")
def get_classes(school_id: int, user: str = Depends(get_current_user)): return database.get_classes_for_school(school_id)

@router.post("/schools/{school_id}/classes")
def create_class(school_id: int, request: ClassCreate, admin: str = Depends(require_admin)):
    if request.strength < 0: raise HTTPException(status_code=400, detail="Class strength cannot be negative.")
    try: return {"status": "success", "data": database.add_class_to_school(school_id, request.name.strip(), request.strength)}
    except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))

@router.delete("/schools/{school_id}/classes/{class_id}")
def delete_class(school_id: int, class_id: int, admin: str = Depends(require_admin)):
    try:
        database.delete_class(school_id, class_id)
        return {"status": "success"}
    except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))

@router.get("/ledger/{school_id}/{class_id}")
def get_ledger(school_id: int, class_id: int, user: str = Depends(get_current_user)): return database.get_ledger_records(school_id, class_id)

@router.post("/ledger/sync")
def sync_ledger(request: SyncRequest, user: str = Depends(get_current_user)):
    try:
        database.sync_ledger_records(request.schoolId, request.classId, request.updates, request.deletes, user)
        return {"status": "success", "synced": len(request.updates) + len(request.deletes)}
    except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.put("/schools/{school_id}/classes/{class_id}/strength")
def update_strength(school_id: int, class_id: int, request: StrengthUpdate, admin: str = Depends(require_admin)):
    if request.strength < 0: 
        raise HTTPException(status_code=400, detail="Class strength cannot be negative.")
    try:
        database.update_class_strength(school_id, class_id, request.strength)
        return {"status": "success"}
    except ValueError as ve: 
        raise HTTPException(status_code=400, detail=str(ve))

@router.get("/ledger/{school_id}/{class_id}/download")
def download_ledger_csv(school_id: int, class_id: int, user: str = Depends(get_current_user)):
    records = database.get_ledger_records(school_id, class_id)
    
    # Get Names for the Filename
    school = next((s for s in database.get_all_schools() if str(s["id"]) == str(school_id)), {})
    school_name = school.get("name", f"School_{school_id}").replace(" ", "_")
    
    classes = database.get_classes_for_school(school_id)
    class_info = next((c for c in classes if str(c["id"]) == str(class_id)), {})
    class_name = class_info.get("name", f"Class_{class_id}").replace(" ", "_")
    strength = class_info.get("strength", 0)
    
    # Generate CSV in memory
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
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Ledger_{school_name}_{class_name}.csv"'}
    )