import os

# Application Settings
APP_NAME = "Vedritam School Stock Ledger Management System"
VERSION = "2.1.0"

# Security Settings
SECRET_KEY = os.getenv("SECRET_KEY", "vedritam_super_secret_key_2026_!@#")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 720

# Signup Settings
SIGNUP_ENABLED = True
SIGNUP_DEFAULT_ROLE = "school"
MIN_PASSWORD_LENGTH = 6

# Database Paths (CSV Files)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_CSV = os.path.join(BASE_DIR, "users.csv")
SCHOOLS_CSV = os.path.join(BASE_DIR, "schools.csv")
LEDGER_CSV = os.path.join(BASE_DIR, "ledger.csv")
AUDIT_CSV = os.path.join(BASE_DIR, "audit.csv")
AI_SETTINGS_JSON = os.path.join(BASE_DIR, "ai_settings.json")

# Distribution, Transfers and Library stores
DISTRIBUTIONS_CSV = os.path.join(BASE_DIR, "distributions.csv")
TRANSFERS_CSV = os.path.join(BASE_DIR, "transfers.csv")
CATALOG_CSV = os.path.join(BASE_DIR, "catalog.csv")
MEMBERS_CSV = os.path.join(BASE_DIR, "members.csv")
LOANS_CSV = os.path.join(BASE_DIR, "loans.csv")
# Library-owned schools, independent from the ledger's SCHOOLS_CSV.
LIBRARY_SCHOOLS_CSV = os.path.join(BASE_DIR, "library_schools.csv")

# Allowed Frontend Files (Security: Prevent Directory Traversal)
ALLOWED_STATIC_FILES = {
    "index.html",
    "dashboard.html",
    "schools.html",
    "ledger.html",
    "reports.html",
    "settings.html",
    "users.html",
    "activity.html",
    "distribution.html",
    "transfers.html",
    "library.html",
    "library-members.html",
    "library-loans.html",
    "todo.html",
    "login.html",
    "style.css",
    "theme.css",
    "dashboard-ui.js",
    "shell.js",
    "app.js",
    "chatbot.js",
    "logo.png",
}
