import os

# Application Settings
APP_NAME = "Vedritam School Stock Ledger Management System"
VERSION = "3.0.0"

# Security Settings
SECRET_KEY = os.getenv("SECRET_KEY", "vedritam_super_secret_key_2026_!@#")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 720

# Signup Settings
SIGNUP_ENABLED = True
# Self sign-up always lands on the lowest privilege level.
SIGNUP_DEFAULT_ROLE = "user"
MIN_PASSWORD_LENGTH = 8

# --- Security policy ---------------------------------------------------------
PASSWORD_REQUIRE_COMPLEXITY = True   # upper + lower + digit
MAX_FAILED_ATTEMPTS = 5              # before temporary lockout
TWOFA_STEPUP_AFTER_FAILURES = 3      # failed passwords that force a 2FA challenge
LOCKOUT_MINUTES = 15
SESSION_IDLE_MINUTES = 30            # idle session timeout
TWOFA_ENFORCED_ROLES = ("super_admin",)  # roles allowed/expected to enrol in 2FA

# Reporting
LOW_STOCK_THRESHOLD = 50

# Database Paths (CSV Files)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_CSV = os.path.join(BASE_DIR, "users.csv")
SCHOOLS_CSV = os.path.join(BASE_DIR, "schools.csv")
LEDGER_CSV = os.path.join(BASE_DIR, "ledger.csv")
AUDIT_CSV = os.path.join(BASE_DIR, "audit.csv")
AI_SETTINGS_JSON = os.path.join(BASE_DIR, "ai_settings.json")

# Master book / notebook / stationery catalog and vendor master
CATALOG_CSV = os.path.join(BASE_DIR, "catalog.csv")
VENDORS_CSV = os.path.join(BASE_DIR, "vendors.csv")

# Standards used across the ledger (PRE KG .. XII plus OTHERS)
STANDARDS = [
    "PRE KG", "LKG", "UKG", "I", "II", "III", "IV", "V",
    "VI", "VII", "VIII", "IX", "X", "XI", "XII", "OTHERS",
]
ARTICLE_CATEGORIES = ["TB", "NB", "STATIONERY", "INHOUSE"]


# Distribution and Transfers stores
DISTRIBUTIONS_CSV = os.path.join(BASE_DIR, "distributions.csv")
TRANSFERS_CSV = os.path.join(BASE_DIR, "transfers.csv")

# Messaging, notifications and security stores
CONVERSATIONS_CSV = os.path.join(BASE_DIR, "conversations.csv")
MESSAGES_CSV = os.path.join(BASE_DIR, "messages.csv")
RECEIPTS_CSV = os.path.join(BASE_DIR, "receipts.csv")
E2EE_KEYS_CSV = os.path.join(BASE_DIR, "e2ee_keys.csv")
NOTIFICATIONS_CSV = os.path.join(BASE_DIR, "notifications.csv")
LOGIN_HISTORY_CSV = os.path.join(BASE_DIR, "login_history.csv")
TYPING_JSON = os.path.join(BASE_DIR, "typing.json")
TWOFA_JSON = os.path.join(BASE_DIR, "twofa.json")

# Attachment storage (messages: images, PDFs, documents)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB
ALLOWED_UPLOAD_TYPES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
    ".csv", ".xlsx", ".xls", ".doc", ".docx", ".txt",
    ".enc",   # end-to-end encrypted attachment blob (server cannot read it)
}
TYPING_TTL_SECONDS = 6

# --- Performance -------------------------------------------------------------
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200
CACHE_TTL_SECONDS = 15

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
    "todo.html",
    "messages.html",
    "security.html",
    "messages.js",
    "login.html",
    "style.css",
    "theme.css",
    "dashboard-ui.js",
    "shell.js",
    "app.js",
    "todo.js",
    "e2ee.js",
    "chatbot.js",
    "logo.png",
    "catalog.csv",
}
