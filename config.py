import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(BASE_DIR / "database.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = str(BASE_DIR / "static" / "uploads")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@nhiem-autotech.local")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
    # Set this to the public HTTPS domain when deploying, e.g. https://example.vn
    SITE_URL = os.getenv("SITE_URL", "").rstrip("/")
    # Optional token supplied by Google Search Console when verifying the domain.
    GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "")
    # QR bank-transfer details. Example BANK_ID: MB, VCB, TCB, ACB.
    BANK_ID = os.getenv("BANK_ID", "")
    BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "")
    BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "")
