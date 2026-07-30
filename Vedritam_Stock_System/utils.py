# utils.py
import hashlib
import os
import tempfile
import csv
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from typing import Dict, List, Any
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

# --- Security & Authentication ---
def hash_password(password: str, salt: str = None) -> str:
    if not salt:
        salt = os.urandom(16).hex()
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
    return f"{salt}${pwd_hash}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        salt, _ = hashed_password.split('$')
        return hashed_password == hash_password(plain_password, salt)
    except ValueError:
        return False

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")

# --- Authoritative Business Logic ---
def calculate_balance(purchased: int, distributed: int, returned: int) -> int:
    """Balance = Purchased - (Distributed + Returned) (Returns increase available stock)"""
    return purchased - (distributed + returned)

def calculate_books_required(strength: int, purchased: int) -> int:
    """Books Required = max(Strength - Purchased, 0)"""
    return max(strength - purchased, 0)

# --- Safe Persistence ---
def atomic_csv_write(filepath: str, headers: List[str], rows: List[Dict[str, Any]]):
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), text=True)
    try:
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                clean_row = {header: row.get(header, "") for header in headers}
                writer.writerow(clean_row)
        os.replace(temp_path, filepath)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise Exception(f"Failed to write CSV securely: {str(e)}")

def current_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")