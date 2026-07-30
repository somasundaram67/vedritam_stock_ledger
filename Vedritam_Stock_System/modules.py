# modules.py
# Data layer for the Distribution, Transfers and Library features.

import csv
import json
import os
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from config import (
    DISTRIBUTIONS_CSV, TRANSFERS_CSV,
    CATALOG_CSV, MEMBERS_CSV, LOANS_CSV,
    LEDGER_CSV, LIBRARY_SCHOOLS_CSV,
)
from utils import atomic_csv_write, current_timestamp
import database

# --- CSV headers -------------------------------------------------------------
DISTRIBUTION_HEADERS = [
    "id", "timestamp", "school_id", "class_id", "ledger_id", "book_name",
    "recipient", "quantity", "remarks", "created_by",
]
TRANSFER_HEADERS = [
    "id", "timestamp", "from_school_id", "to_school_id", "book_name",
    "quantity", "status", "remarks", "created_by", "approved_by", "approved_at",
    "decision_remarks",
]
CATALOG_HEADERS = [
    "id", "school_id", "accession", "title", "author", "publisher",
    "category", "copies", "available", "remarks", "created_by", "created_time",
]
MEMBER_HEADERS = [
    "id", "school_id", "uid", "name", "class_name", "section",
    "created_by", "created_time",
]
LOAN_HEADERS = [
    "id", "school_id", "catalog_id", "member_id", "issued_at",
    "due_at", "returned_at", "status", "remarks", "created_by",
]
LIBRARY_SCHOOL_HEADERS = [
    "id", "name", "code", "location", "created_by", "created_time",
]


def _read(path, headers):
    if not os.path.exists(path):
        atomic_csv_write(path, headers, [])
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def init_stores():
    for p, h in [
        (DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS),
        (TRANSFERS_CSV, TRANSFER_HEADERS),
        (CATALOG_CSV, CATALOG_HEADERS),
        (MEMBERS_CSV, MEMBER_HEADERS),
        (LOANS_CSV, LOAN_HEADERS),
        (LIBRARY_SCHOOLS_CSV, LIBRARY_SCHOOL_HEADERS),
    ]:
        if not os.path.exists(p):
            atomic_csv_write(p, h, [])


# ============================================================================
# LIBRARY SCHOOLS — independent from the ledger's schools list.
# ============================================================================
def list_library_schools() -> List[Dict]:
    return _read(LIBRARY_SCHOOLS_CSV, LIBRARY_SCHOOL_HEADERS)


def create_library_school(name: str, code: str, location: str, username: str) -> Dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("School name is required.")
    rows = _read(LIBRARY_SCHOOLS_CSV, LIBRARY_SCHOOL_HEADERS)
    if any((r.get("name") or "").lower() == name.lower() for r in rows):
        raise ValueError("A library school with that name already exists.")
    next_id = 1
    for r in rows:
        try:
            next_id = max(next_id, int(r.get("id") or 0) + 1)
        except Exception:
            pass
    rec = {
        "id": str(next_id),
        "name": name,
        "code": (code or "").strip(),
        "location": (location or "").strip(),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(rec)
    atomic_csv_write(LIBRARY_SCHOOLS_CSV, LIBRARY_SCHOOL_HEADERS, rows)
    return rec


def delete_library_school(school_id) -> None:
    rows = _read(LIBRARY_SCHOOLS_CSV, LIBRARY_SCHOOL_HEADERS)
    kept = [r for r in rows if str(r.get("id")) != str(school_id)]
    if len(kept) == len(rows):
        raise ValueError("Library school not found.")
    atomic_csv_write(LIBRARY_SCHOOLS_CSV, LIBRARY_SCHOOL_HEADERS, kept)


# ============================================================================
# DISTRIBUTION — issue books from a class ledger row to a class/teacher.
# ============================================================================
def list_distributions(school_id_filter: str = "") -> List[Dict]:
    rows = _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS)
    if school_id_filter:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id_filter)]
    rows.reverse()
    return rows


def create_distribution(school_id, class_id, ledger_id, recipient, quantity, remarks, username):
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    # Locate the ledger row and increment its `distributed` counter atomically.
    ledger = database.read_csv(LEDGER_CSV)
    target = next((r for r in ledger if str(r["id"]) == str(ledger_id)
                   and str(r["school_id"]) == str(school_id)
                   and str(r["class_id"]) == str(class_id)), None)
    if not target:
        raise ValueError("Book row not found for this class.")
    purchased = int(target.get("purchased") or 0)
    distributed = int(target.get("distributed") or 0)
    returned = int(target.get("returned") or 0)
    balance = purchased - distributed - returned
    if qty > balance:
        raise ValueError(f"Only {balance} books available in stock.")
    target["distributed"] = str(distributed + qty)
    target["balance"] = str(balance - qty)
    target["modified_by"] = username
    target["modified_time"] = current_timestamp()
    atomic_csv_write(LEDGER_CSV, database.LEDGER_HEADERS, ledger)

    rows = _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS)
    record = {
        "id": f"D_{uuid.uuid4().hex[:8]}",
        "timestamp": current_timestamp(),
        "school_id": str(school_id),
        "class_id": str(class_id),
        "ledger_id": str(ledger_id),
        "book_name": target.get("bookName", ""),
        "recipient": (recipient or "").strip(),
        "quantity": str(qty),
        "remarks": (remarks or "").strip(),
        "created_by": username,
    }
    rows.append(record)
    atomic_csv_write(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS, rows)
    return record


# ============================================================================
# TRANSFERS — move stock between schools with a request/approve workflow.
# ============================================================================
def list_transfers(school_id_filter: str = "") -> List[Dict]:
    rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
    if school_id_filter:
        rows = [r for r in rows
                if str(r.get("from_school_id")) == str(school_id_filter)
                or str(r.get("to_school_id")) == str(school_id_filter)]
    rows.reverse()
    return rows


def create_transfer(from_school_id, to_school_id, book_name, quantity, remarks, username):
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    if str(from_school_id) == str(to_school_id):
        raise ValueError("Source and destination schools must differ.")
    rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
    record = {
        "id": f"T_{uuid.uuid4().hex[:8]}",
        "timestamp": current_timestamp(),
        "from_school_id": str(from_school_id),
        "to_school_id": str(to_school_id),
        "book_name": (book_name or "").strip(),
        "quantity": str(qty),
        "status": "Pending",
        "remarks": (remarks or "").strip(),
        "created_by": username,
        "approved_by": "",
        "approved_at": "",
        "decision_remarks": "",
    }
    rows.append(record)
    atomic_csv_write(TRANSFERS_CSV, TRANSFER_HEADERS, rows)
    return record


def get_transfer(transfer_id: str) -> Optional[Dict]:
    for r in _read(TRANSFERS_CSV, TRANSFER_HEADERS):
        if r.get("id") == transfer_id:
            return r
    return None


def set_transfer_status(transfer_id: str, status: str, username: str, remarks: str = ""):
    if status not in ("Approved", "Rejected", "Completed"):
        raise ValueError("Invalid status.")
    rows = _read(TRANSFERS_CSV, TRANSFER_HEADERS)
    tgt = next((r for r in rows if r["id"] == transfer_id), None)
    if not tgt:
        raise ValueError("Transfer not found.")
    tgt["status"] = status
    tgt["approved_by"] = username
    tgt["approved_at"] = current_timestamp()
    if remarks:
        tgt["decision_remarks"] = remarks
    atomic_csv_write(TRANSFERS_CSV, TRANSFER_HEADERS, rows)
    return tgt


def get_distribution(dist_id: str) -> Optional[Dict]:
    for r in _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS):
        if r.get("id") == dist_id:
            return r
    return None


def delete_distribution(dist_id: str, username: str) -> Dict:
    """Reverse a distribution: put the books back into the ledger row's balance."""
    rows = _read(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS)
    rec = next((r for r in rows if r.get("id") == dist_id), None)
    if not rec:
        raise ValueError("Distribution not found.")
    qty = int(rec.get("quantity") or 0)
    ledger = database.read_csv(LEDGER_CSV)
    tgt = next((r for r in ledger if str(r["id"]) == str(rec.get("ledger_id"))), None)
    if tgt:
        distributed = max(int(tgt.get("distributed") or 0) - qty, 0)
        balance = int(tgt.get("balance") or 0) + qty
        tgt["distributed"] = str(distributed)
        tgt["balance"] = str(balance)
        tgt["modified_by"] = username
        tgt["modified_time"] = current_timestamp()
        atomic_csv_write(LEDGER_CSV, database.LEDGER_HEADERS, ledger)
    kept = [r for r in rows if r.get("id") != dist_id]
    atomic_csv_write(DISTRIBUTIONS_CSV, DISTRIBUTION_HEADERS, kept)
    return rec


# ============================================================================
# LIBRARY: catalog, members, loans
# ============================================================================
def list_catalog(school_id_filter: str = "") -> List[Dict]:
    rows = _read(CATALOG_CSV, CATALOG_HEADERS)
    if school_id_filter:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id_filter)]
    return rows


def create_catalog(school_id, data, username) -> Dict:
    accession = (data.get("accession") or "").strip()
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Book title is required.")
    copies = int(data.get("copies") or 1)
    if copies < 1:
        raise ValueError("Copies must be at least 1.")
    rows = _read(CATALOG_CSV, CATALOG_HEADERS)
    if accession and any(r for r in rows
                         if str(r.get("school_id")) == str(school_id)
                         and (r.get("accession") or "").lower() == accession.lower()):
        raise ValueError("Accession number already exists in this school.")
    record = {
        "id": f"B_{uuid.uuid4().hex[:8]}",
        "school_id": str(school_id),
        "accession": accession,
        "title": title,
        "author": (data.get("author") or "").strip(),
        "publisher": (data.get("publisher") or "").strip(),
        "category": (data.get("category") or "").strip(),
        "copies": str(copies),
        "available": str(copies),
        "remarks": (data.get("remarks") or "").strip(),
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, rows)
    return record


def delete_catalog(book_id: str):
    rows = _read(CATALOG_CSV, CATALOG_HEADERS)
    kept = [r for r in rows if r["id"] != book_id]
    if len(kept) == len(rows):
        raise ValueError("Book not found.")
    atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, kept)


def list_members(school_id_filter: str = "") -> List[Dict]:
    rows = _read(MEMBERS_CSV, MEMBER_HEADERS)
    if school_id_filter:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id_filter)]
    return rows


def create_member(school_id, data, username) -> Dict:
    name = (data.get("name") or "").strip()
    uid = (data.get("uid") or "").strip()
    cls = (data.get("class_name") or "").strip()
    section = (data.get("section") or "").strip()
    if not (name and uid and cls and section):
        raise ValueError("Name, class, section and UID are all required.")
    rows = _read(MEMBERS_CSV, MEMBER_HEADERS)
    if any(r for r in rows
           if str(r.get("school_id")) == str(school_id)
           and (r.get("uid") or "").lower() == uid.lower()):
        raise ValueError(f"UID '{uid}' is already registered in this school.")
    record = {
        "id": f"M_{uuid.uuid4().hex[:8]}",
        "school_id": str(school_id),
        "uid": uid,
        "name": name,
        "class_name": cls,
        "section": section,
        "created_by": username,
        "created_time": current_timestamp(),
    }
    rows.append(record)
    atomic_csv_write(MEMBERS_CSV, MEMBER_HEADERS, rows)
    return record


def delete_member(member_id: str):
    rows = _read(MEMBERS_CSV, MEMBER_HEADERS)
    kept = [r for r in rows if r["id"] != member_id]
    if len(kept) == len(rows):
        raise ValueError("Member not found.")
    atomic_csv_write(MEMBERS_CSV, MEMBER_HEADERS, kept)


def _loan_status(row: Dict) -> str:
    if row.get("returned_at"):
        return "Returned"
    try:
        due = datetime.strptime(row.get("due_at", "")[:10], "%Y-%m-%d")
        if due < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
            return "Overdue"
    except Exception:
        pass
    return "Issued"


def list_loans(school_id_filter: str = "") -> List[Dict]:
    rows = _read(LOANS_CSV, LOAN_HEADERS)
    if school_id_filter:
        rows = [r for r in rows if str(r.get("school_id")) == str(school_id_filter)]

    books = {b["id"]: b for b in _read(CATALOG_CSV, CATALOG_HEADERS)}
    members = {m["id"]: m for m in _read(MEMBERS_CSV, MEMBER_HEADERS)}
    out = []
    for r in rows:
        b = books.get(r.get("catalog_id"), {})
        m = members.get(r.get("member_id"), {})
        out.append({
            **r,
            "status": _loan_status(r),
            "book_title": b.get("title", ""),
            "book_accession": b.get("accession", ""),
            "member_name": m.get("name", ""),
            "member_uid": m.get("uid", ""),
            "member_class": (m.get("class_name", "") + (" " + m.get("section", "") if m.get("section") else "")).strip(),
        })
    out.reverse()
    return out


def create_loan(school_id, catalog_id, member_id, due_at, remarks, username):
    if not due_at:
        raise ValueError("Due date is required.")
    # Validate the due date format up-front.
    try:
        datetime.strptime(due_at[:10], "%Y-%m-%d")
    except Exception:
        raise ValueError("Due date must be YYYY-MM-DD.")

    books = _read(CATALOG_CSV, CATALOG_HEADERS)
    book = next((b for b in books if b["id"] == catalog_id
                 and str(b["school_id"]) == str(school_id)), None)
    if not book:
        raise ValueError("Book not found in this school's library.")
    available = int(book.get("available") or 0)
    if available < 1:
        raise ValueError("No copies of this book are currently available.")
    members = _read(MEMBERS_CSV, MEMBER_HEADERS)
    member = next((m for m in members if m["id"] == member_id
                   and str(m["school_id"]) == str(school_id)), None)
    if not member:
        raise ValueError("Member not found in this school.")

    book["available"] = str(available - 1)
    atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, books)

    loans = _read(LOANS_CSV, LOAN_HEADERS)
    record = {
        "id": f"LN_{uuid.uuid4().hex[:8]}",
        "school_id": str(school_id),
        "catalog_id": catalog_id,
        "member_id": member_id,
        "issued_at": current_timestamp(),
        "due_at": due_at[:10],
        "returned_at": "",
        "status": "Issued",
        "remarks": (remarks or "").strip(),
        "created_by": username,
    }
    loans.append(record)
    atomic_csv_write(LOANS_CSV, LOAN_HEADERS, loans)
    return record


def return_loan(loan_id: str, username: str):
    loans = _read(LOANS_CSV, LOAN_HEADERS)
    ln = next((r for r in loans if r["id"] == loan_id), None)
    if not ln:
        raise ValueError("Loan not found.")
    if ln.get("returned_at"):
        raise ValueError("This loan has already been closed.")
    ln["returned_at"] = current_timestamp()
    ln["status"] = "Returned"
    atomic_csv_write(LOANS_CSV, LOAN_HEADERS, loans)

    books = _read(CATALOG_CSV, CATALOG_HEADERS)
    book = next((b for b in books if b["id"] == ln.get("catalog_id")), None)
    if book:
        book["available"] = str(int(book.get("available") or 0) + 1)
        atomic_csv_write(CATALOG_CSV, CATALOG_HEADERS, books)
    return ln


def library_reminders(school_id_filter: str = "") -> Dict[str, Any]:
    """Returns due-soon + overdue counts and rows for the notification bell."""
    loans = list_loans(school_id_filter)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    soon = today + timedelta(days=3)
    overdue, due_soon = [], []
    for l in loans:
        if l.get("returned_at"):
            continue
        try:
            due = datetime.strptime(l.get("due_at", "")[:10], "%Y-%m-%d")
        except Exception:
            continue
        if due < today:
            overdue.append(l)
        elif due <= soon:
            due_soon.append(l)
    return {"overdue": overdue, "due_soon": due_soon,
            "counts": {"overdue": len(overdue), "due_soon": len(due_soon)}}


def library_context_json(school_id_filter: str = "") -> str:
    """Compact JSON snapshot the chatbot can quote in Library mode."""
    books = list_catalog(school_id_filter)
    members = list_members(school_id_filter)
    loans = list_loans(school_id_filter)
    payload = {
        "catalog": [{"title": b.get("title"), "accession": b.get("accession"),
                     "author": b.get("author"), "copies": b.get("copies"),
                     "available": b.get("available")} for b in books],
        "members": [{"uid": m.get("uid"), "name": m.get("name"),
                     "class": m.get("class_name"), "section": m.get("section")}
                    for m in members],
        "loans": [{"book": l.get("book_title"), "accession": l.get("book_accession"),
                   "member": l.get("member_name"), "uid": l.get("member_uid"),
                   "class": l.get("member_class"),
                   "issued": l.get("issued_at"), "due": l.get("due_at"),
                   "returned": l.get("returned_at"), "status": l.get("status")}
                  for l in loans],
    }
    return json.dumps(payload)[:12000]
