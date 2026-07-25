import os

# Application Settings
APP_NAME = "Vedritam School Stock Ledger Management System"
VERSION = "1.0.0"

# Security Settings
SECRET_KEY = os.getenv("SECRET_KEY", "vedritam_super_secret_key_2026_!@#")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 720  # 12 hours (covers a full school shift)

# Database Paths (CSV Files)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_CSV = os.path.join(BASE_DIR, "users.csv")
SCHOOLS_CSV = os.path.join(BASE_DIR, "schools.csv")
LEDGER_CSV = os.path.join(BASE_DIR, "ledger.csv")

# Allowed Frontend Files (Security: Prevent Directory Traversal)
ALLOWED_STATIC_FILES = {
    "index.html", 
    "dashboard.html", 
    "schools.html", 
    "ledger.html", 
    "users.html", 
    "style.css", 
    "app.js", 
    "logo.png",
    "image_2c83bf.png"
}
