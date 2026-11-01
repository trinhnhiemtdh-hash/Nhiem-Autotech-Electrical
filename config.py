import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-key")
    raw_db_url = os.getenv("DATABASE_URL", "postgresql://postgres.holaxcmiqrduhechsxsk:Trinhnhiem321%40@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres")
    if raw_db_url and raw_db_url.startswith("postgres://"):
        raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = raw_db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_timeout": 15,
    }
    UPLOAD_FOLDER = str(BASE_DIR / "static" / "uploads")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "trinhnhiemtdh@gmail.com")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123456")
    
    # Domain công khai khi đưa lên Internet
    SITE_URL = os.getenv("SITE_URL", "https://nhiemautotech.io.vn").rstrip("/")
    
    # Mã xác minh Google Search Console
    GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "")
    
    # THÔNG TIN TÀI KHOẢN NGÂN HÀNG VIETCOMBANK & VIETQR
    BANK_ID = os.getenv("BANK_ID", "VCB")
    BANK_ACCOUNT = os.getenv("BANK_ACCOUNT", "1016703323")
    BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "TRINH DUC NHIEM")
    BANK_BRANCH = os.getenv("BANK_BRANCH", "PGD Ea Kar")
    
    # CẤU HÌNH GỬI EMAIL TỰ ĐỘNG (GMAIL SMTP)
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() in ["true", "1", "yes"]
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "trinhnhiemtdh@gmail.com")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")  # Mật khẩu ứng dụng Gmail (App Password 16 ký tự)
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "Nhiệm Autotech Electrical <trinhnhiemtdh@gmail.com>")
