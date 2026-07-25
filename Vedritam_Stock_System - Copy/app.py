# app.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from api import router
import database
from config import APP_NAME, VERSION, ALLOWED_STATIC_FILES, BASE_DIR

app = FastAPI(title=APP_NAME, version=VERSION)

# Enable CORS for frontend interoperability
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup Event: Initialize Database constraints and CSV files
@app.on_event("startup")
def startup_event():
    print("Starting Vedritam School Stock Ledger Management System...")
    database.init_db()

# Hook up the API Router
app.include_router(router)

# --- Secure Frontend File Serving ---
# We deliberately avoid using `StaticFiles(directory=".")` to prevent 
# malicious users from downloading the .csv database files.

@app.get("/")
def serve_root():
    """Serves the login gateway by default."""
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/{filename}")
def serve_static(filename: str):
    """
    Strictly serves only predefined safe frontend files.
    Rejects any request for .csv, .py, or unknown files.
    """
    if filename in ALLOWED_STATIC_FILES:
        filepath = os.path.join(BASE_DIR, filename)
        if os.path.exists(filepath):
            return FileResponse(filepath)
    
    # Fallback/Security rejection
    raise HTTPException(status_code=404, detail="File not found or access denied.")

# --- Entry Point ---
if __name__ == "__main__":
    import uvicorn
    # Run the server on port 8000
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)