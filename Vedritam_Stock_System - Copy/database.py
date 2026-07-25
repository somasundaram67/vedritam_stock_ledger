# database.py
import csv
import json
import uuid
import os
from typing import List, Dict, Any
from utils import atomic_csv_write, hash_password, current_timestamp, calculate_balance, calculate_books_required
from config import USERS_CSV, SCHOOLS_CSV, LEDGER_CSV

SCHOOL_HEADERS = ["id", "name", "code", "location", "classes_json"]
LEDGER_HEADERS = [
    "id", "school_id", "class_id", "bookName", "subject", "publication", "vendor", "category", 
    "invoiceRef", "strength", "purchased", "distributed", "returned", "balance", "booksRequired", "remarks", 
    "created_by", "created_time", "modified_by", "modified_time"
]

def init_db():
    if not os.path.exists(USERS_CSV):
        headers = ["username", "password_hash", "role", "lastLogin", "status"]
        atomic_csv_write(USERS_CSV, headers, [
            {"username": "admin", "password_hash": hash_password("admin123"), "role": "admin", "lastLogin": current_timestamp(), "status": "Active"}
        ])

    if not os.path.exists(SCHOOLS_CSV):
        atomic_csv_write(SCHOOLS_CSV, SCHOOL_HEADERS, [
            {"id": "1", "name": "Delhi Public School", "code": "DPS-01", "location": "North Zone", 
             "classes_json": json.dumps([{"id": 101, "name": "Class 9 - A", "strength": 40}])}
        ])

    if not os.path.exists(LEDGER_CSV):
        atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, [{
            "id": "L1001", "school_id": "1", "class_id": "101", "bookName": "Mathematics X",
            "subject": "Math", "publication": "NCERT", "vendor": "OBS", "category": "Textbook",
            "invoiceRef": "INV-1", "strength": "40", "purchased": "35", "distributed": "20", "returned": "2", 
            "balance": "17", "booksRequired": "5", "remarks": "", "created_by": "admin", 
            "created_time": current_timestamp(), "modified_by": "admin", "modified_time": current_timestamp()
        }])

def read_csv(filepath: str) -> List[Dict[str, Any]]:
    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []

# --- Users Logic ---
def get_user_by_username(username: str) -> Dict:
    for u in read_csv(USERS_CSV):
        if u['username'] == username and u['status'] == 'Active': 
            return u
    return None

def update_user_login(username: str):
    users = read_csv(USERS_CSV)
    for u in users:
        if u['username'] == username: 
            u['lastLogin'] = current_timestamp()
    atomic_csv_write(USERS_CSV, ["username", "password_hash", "role", "lastLogin", "status"], users)

# --- Schools & Classes Logic ---
def get_all_schools() -> List[Dict]:
    return [{"id": int(s["id"]), "name": s["name"], "code": s["code"], "location": s["location"]} for s in read_csv(SCHOOLS_CSV)]

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

# --- Ledger Logic ---
def get_ledger_records(school_id: int, class_id: int) -> List[Dict]:
    ledger = read_csv(LEDGER_CSV)
    result = [r for r in ledger if str(r["school_id"]) == str(school_id) and str(r["class_id"]) == str(class_id)]
    for r in result:
        for f in ['purchased', 'distributed', 'returned', 'balance', 'booksRequired', 'strength']:
            r[f] = int(r.get(f) or 0)
    return result

def sync_ledger_records(school_id: int, class_id: int, updates: List[Dict], deletes: List[str], username: str):
    # Authoritative Class Lookup for Default Strength
    target_class = next((c for c in get_classes_for_school(school_id) if str(c["id"]) == str(class_id)), None)
    if not target_class: 
        raise ValueError("Class not found.")
    default_class_strength = int(target_class["strength"])

    ledger = read_csv(LEDGER_CSV)
    delete_set = set(deletes)
    ledger = [row for row in ledger if str(row["id"]) not in delete_set]
    update_dict = {str(u["id"]): u for u in updates}
    
    # Process and validate row modifications
    def process_row(row_data, incoming_mod):
        for key in LEDGER_HEADERS:
            if key in incoming_mod and key not in ['id', 'school_id', 'class_id', 'balance', 'booksRequired']:
                row_data[key] = incoming_mod[key]
        try:
            p = int(row_data.get("purchased") or 0)
            d = int(row_data.get("distributed") or 0)
            r = int(row_data.get("returned") or 0)
            
            # Read row-specific strength if provided, otherwise fall back to default class strength
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

    # Phase A: Updates
    for row in ledger:
        rid = str(row["id"])
        if rid in update_dict:
            process_row(row, update_dict[rid])
            del update_dict[rid]
            
    # Phase B: Inserts
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
        ledger.insert(0, record)
        
    atomic_csv_write(LEDGER_CSV, LEDGER_HEADERS, ledger)