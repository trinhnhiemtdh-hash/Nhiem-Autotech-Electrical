from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_, inspect, text
from models import db, User, Category, Product, Order, OrderItem, Contact, Download, PageVisit
from config import Config
from functools import wraps
from pathlib import Path
from datetime import timezone
from urllib.parse import quote as url_quote, urlencode
from xml.sax.saxutils import escape
import os, json, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

def initialize_database():
    """Create and safely upgrade the SQLite database on startup."""
    db.create_all()
    columns_cat = {column["name"] for column in inspect(db.engine).get_columns("category")}
    if "image" not in columns_cat:
        db.session.execute(text("ALTER TABLE category ADD COLUMN image VARCHAR(255)"))
    
    columns_order = {column["name"] for column in inspect(db.engine).get_columns("order")}
    if "email" not in columns_order:
        db.session.execute(text("ALTER TABLE \"order\" ADD COLUMN email VARCHAR(180)"))
        
    columns_prod = {column["name"] for column in inspect(db.engine).get_columns("product")}
    if "sort_order" not in columns_prod:
        db.session.execute(text("ALTER TABLE product ADD COLUMN sort_order INTEGER DEFAULT 0"))

    if "page_visit" in inspect(db.engine).get_table_names():
        columns_visit = {column["name"] for column in inspect(db.engine).get_columns("page_visit")}
        if "user_id" not in columns_visit:
            db.session.execute(text("ALTER TABLE page_visit ADD COLUMN user_id INTEGER"))
        if "location" not in columns_visit:
            db.session.execute(text("ALTER TABLE page_visit ADD COLUMN location VARCHAR(100) DEFAULT 'Việt Nam'"))
        if "device_type" not in columns_visit:
            db.session.execute(text("ALTER TABLE page_visit ADD COLUMN device_type VARCHAR(50) DEFAULT 'Máy tính'"))
        
    default_categories = [
        ("Thư viện AutoCAD", "thu-vien-autocad"),
        ("Khóa học & Layout Tủ điện", "thiet-ke-tu-dien"),
        ("Mô hình & Đồ án Tự động hóa", "do-an-tu-dong-hoa"),
    ]
    for name, slug in default_categories:
        if not Category.query.filter_by(slug=slug).first():
            db.session.add(Category(name=name, slug=slug))
    if not User.query.filter_by(email=app.config["ADMIN_EMAIL"]).first():
        db.session.add(User(name="Administrator", email=app.config["ADMIN_EMAIL"],
                            password_hash=generate_password_hash(app.config["ADMIN_PASSWORD"]), is_admin=True))
    db.session.commit()
    
    if Product.query.count() == 0:
        json_path = Path(__file__).resolve().parent / "products_dump.json"
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    products_data = json.load(f)
                cat_map = {c.slug: c.id for c in Category.query.all()}
                for item in products_data:
                    cat_id = cat_map.get(item.get("category_slug"), None)
                    p = Product(
                        category_id=cat_id,
                        name=item.get("name"),
                        slug=item.get("slug"),
                        sku=item.get("sku"),
                        price=item.get("price", 0),
                        stock=item.get("stock", 999),
                        sort_order=item.get("sort_order", 0),
                        image=item.get("image", ""),
                        images=item.get("images", ""),
                        cad_file=item.get("cad_file", ""),
                        description=item.get("description", ""),
                        specifications=item.get("specifications", ""),
                        status=item.get("status", "active")
                    )
                    db.session.add(p)
                db.session.commit()
                print(f"[INIT] Auto-seeded {len(products_data)} products into database.")
            except Exception as e:
                print(f"[INIT ERROR] Failed to seed products: {e}")

with app.app_context():
    initialize_database()

def start_self_ping():
    """Background thread to ping public site URL every 4 minutes to keep Render awake."""
    import threading, time, urllib.request
    def ping_loop():
        time.sleep(20)
        while True:
            try:
                target_url = "https://autotech-electrical.onrender.com/ping"
                req = urllib.request.Request(target_url, headers={"User-Agent": "NhiemAutotechSelfPing/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    print(f"[KEEP-ALIVE] Self ping status: {resp.status}")
            except Exception as e:
                print(f"[KEEP-ALIVE] Self ping error: {e}")
            time.sleep(240)

    t = threading.Thread(target=ping_loop, daemon=True)
    t.start()

start_self_ping()

ALLOWED_DOCS = {"dwg", "dxf", "pdf", "zip", "rar", "7z", "sldprt", "sldasm", "step", "stp", "iges", "igs", "xlsx", "docx", "pptx", "mp4", "zap", "mwp", "cxp"}
ALLOWED_VIDEOS = {"mp4", "webm", "mov", "m4v", "avi", "mkv"}

def public_url(endpoint, **values):
    """Build a canonical public URL, independent of local development host."""
    relative_url = url_for(endpoint, **values)
    if app.config["SITE_URL"]:
        return f"{app.config['SITE_URL']}{relative_url}"
    return url_for(endpoint, _external=True, **values)

def send_order_paid_email(order):
    """Send order confirmation email with product details, direct CAD download links, and direct attachments to customer's Gmail."""
    customer_email = order.email or (order.user.email if order.user else None)
    if not customer_email:
        print(f"[MAIL WARN] No customer email found for order #{order.id}")
        return False

    smtp_user = app.config.get("MAIL_USERNAME")
    smtp_pass = app.config.get("MAIL_PASSWORD")
    smtp_server = app.config.get("MAIL_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_PORT", 587)
    smtp_tls = app.config.get("MAIL_USE_TLS", True)
    sender = app.config.get("MAIL_DEFAULT_SENDER") or (smtp_user if smtp_user else "nhiem.autotech@gmail.com")

    cad_dir = Path(app.config["UPLOAD_FOLDER"]) / "cad"
    attached_files = []

    # Build products list with download links
    items_html = ""
    for item in order.items:
        p = db.session.get(Product, item.product_id) if item.product_id else None
        download_html = ""
        if p and p.cad_file:
            dl_url = public_url("cad_download", product_id=p.id, order_id=order.id)
            download_html = f'<div style="margin-top:8px;"><a href="{dl_url}" style="background:#ff7a00;color:#ffffff;padding:8px 18px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;font-size:13px;box-shadow:0 2px 6px rgba(255,122,0,0.25);">⬇ Tải file CAD ({p.cad_file})</a></div>'
            
            # Check if file exists on disk to attach
            file_on_disk = cad_dir / p.cad_file
            if file_on_disk.exists() and file_on_disk.is_file():
                if file_on_disk.stat().st_size <= 24 * 1024 * 1024:
                    attached_files.append((p.cad_file, file_on_disk))
        else:
            download_html = '<div style="margin-top:4px;color:#64748b;font-size:12px;">(Thiết bị vật tư / Tài liệu kỹ thuật)</div>'

        items_html += f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:14px 10px;">
            <strong style="color:#0f172a;font-size:14.5px;">{item.product_name}</strong>
            {download_html}
          </td>
          <td style="padding:14px 10px;text-align:center;font-weight:600;">{item.quantity}</td>
          <td style="padding:14px 10px;text-align:right;color:#ff7a00;font-weight:bold;">{item.price:,.0f} đ</td>
          <td style="padding:14px 10px;text-align:right;font-weight:bold;color:#0f172a;">{item.subtotal:,.0f} đ</td>
        </tr>
        """

    orders_url = public_url("account_orders")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;color:#1e293b;background-color:#f8fafc;margin:0;padding:24px;">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;box-shadow:0 6px 20px rgba(0,0,0,0.06);">
        <!-- Header -->
        <div style="background:#0f172a;padding:26px 20px;text-align:center;border-bottom:3px solid #ff7a00;">
          <h1 style="color:#ffffff;margin:0;font-size:22px;letter-spacing:1px;">NHIỆM AUTOTECH ELECTRICAL</h1>
          <p style="color:#ff9933;margin:6px 0 0;font-size:13.5px;font-weight:bold;letter-spacing:0.5px;">XÁC NHẬN THANH TOÁN & GIAO FILE BẢN VẼ CAD THÀNH CÔNG</p>
        </div>
        
        <!-- Body -->
        <div style="padding:28px 24px;">
          <p style="font-size:15px;margin-top:0;">Kính chào quý khách <strong>{order.customer_name}</strong>,</p>
          <p style="font-size:14px;color:#334155;">
            <strong>Nhiệm Autotech Electrical</strong> xin trân trọng cảm ơn quý khách! Đơn hàng <strong>#{order.id}</strong> của quý khách đã được Admin xác nhận thanh toán thành công qua tài khoản Vietcombank.
          </p>

          <div style="background:#f1f5f9;border-radius:8px;padding:16px 18px;margin:20px 0;border-left:4px solid #ff7a00;">
            <div style="font-size:13.5px;color:#475569;">Mã đơn hàng: <strong style="color:#0f172a;">#{order.id}</strong></div>
            <div style="font-size:13.5px;color:#475569;margin-top:4px;">Gmail nhận file: <strong style="color:#0284c7;">{customer_email}</strong></div>
            <div style="font-size:13.5px;color:#475569;margin-top:4px;">Số điện thoại: <strong style="color:#0f172a;">{order.phone}</strong></div>
            <div style="font-size:13.5px;color:#475569;margin-top:4px;">Tổng số tiền đã thanh toán: <strong style="color:#ff7a00;font-size:16px;">{order.total:,.0f} đ</strong></div>
            <div style="font-size:13.5px;color:#475569;margin-top:4px;">Tài khoản nhận: Vietcombank - 1016703323 (TRINH DUC NHIEM)</div>
          </div>

          <h3 style="color:#0f172a;font-size:16px;border-bottom:2px solid #f1f5f9;padding-bottom:8px;margin-bottom:12px;">Chi tiết sản phẩm & Link tải file CAD:</h3>
          <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:2px solid #e2e8f0;font-size:12.5px;color:#64748b;">
                <th style="padding:10px 8px;text-align:left;">Sản phẩm / Bản vẽ</th>
                <th style="padding:10px 8px;text-align:center;">SL</th>
                <th style="padding:10px 8px;text-align:right;">Đơn giá</th>
                <th style="padding:10px 8px;text-align:right;">Thành tiền</th>
              </tr>
            </thead>
            <tbody>
              {items_html}
            </tbody>
          </table>

          <div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:14px;margin-bottom:24px;color:#065f46;font-size:13px;">
            💡 <strong>Lưu ý:</strong> Quý khách có thể bấm nút <strong>"Tải file CAD"</strong> màu cam ở trên để tải trực tiếp bản vẽ về máy tính. Nếu file được đính kèm, quý khách có thể tải trực tiếp file đính kèm dưới email này.
          </div>

          <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">

          <!-- Contact Support -->
          <div style="font-size:13px;color:#64748b;line-height:1.6;">
            <p style="margin:0 0 6px;"><strong style="color:#0f172a;">Cần hỗ trợ kỹ thuật hoặc hướng dẫn sử dụng file CAD?</strong></p>
            <p style="margin:0 0 4px;">📞 Hotline / Zalo: <strong style="color:#ff7a00;">0379 760 905</strong></p>
            <p style="margin:0 0 4px;">🌐 Facebook: <a href="https://www.facebook.com/kois.nhims.1/" style="color:#0284c7;">fb.com/kois.nhims.1</a></p>
            <p style="margin:0 0 4px;">🎵 TikTok: <a href="https://www.tiktok.com/@nhim.autotech" style="color:#0284c7;">@nhim.autotech</a></p>
          </div>
        </div>

        <!-- Footer -->
        <div style="background:#f8fafc;padding:16px;text-align:center;font-size:12px;color:#94a3b8;border-top:1px solid #e2e8f0;">
          © 2026 Nhiệm Autotech Electrical · Thiết bị điện công nghiệp · AutoCAD · Tủ điện
        </div>
      </div>
    </body>
    </html>
    """

    if not smtp_user or not smtp_pass:
        print(f"[MAIL INFO] SMTP not configured (MAIL_USERNAME/MAIL_PASSWORD empty). Ready to email {customer_email} for order #{order.id}")
        return False

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"[Nhiệm Autotech] Xác nhận thanh toán & Giao file bản vẽ đơn hàng #{order.id}"
        msg["From"] = sender
        msg["To"] = customer_email

        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(html_content, "html", "utf-8"))
        msg.attach(alt_part)

        # Attach CAD files if available
        for filename, filepath in attached_files:
            try:
                with open(filepath, "rb") as f:
                    part = MIMEApplication(f.read(), Name=filename)
                part["Content-Disposition"] = f'attachment; filename="{filename}"'
                msg.attach(part)
                print(f"[MAIL INFO] Attached file {filename} to email for order #{order.id}")
            except Exception as fe:
                print(f"[MAIL WARN] Could not attach file {filename}: {fe}")

        if smtp_tls:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=12)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=12)

        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, [customer_email], msg.as_string())
        server.quit()
        print(f"[MAIL SUCCESS] Email and CAD files sent to {customer_email} for order #{order.id}")
        return True
    except Exception as e:
        print(f"[MAIL ERROR] Could not send email to {customer_email}: {e}")
        return False

def get_device_type(ua_str):
    if not ua_str:
        return "💻 Máy tính"
    ua_lower = ua_str.lower()
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        return "📱 Điện thoại"
    elif "ipad" in ua_lower or "tablet" in ua_lower:
        return "📱 Máy tính bảng"
    return "💻 Máy tính"

_IP_LOCATION_CACHE = {}

def get_location_name(ip):
    if not ip or ip in ["127.0.0.1", "::1", "localhost"] or ip.startswith("192.168.") or ip.startswith("10."):
        return "🏠 Nội bộ (Localhost)"
    
    if ip in _IP_LOCATION_CACHE:
        return _IP_LOCATION_CACHE[ip]

    location_str = "🇻🇳 Việt Nam"
    try:
        import urllib.request
        req = urllib.request.Request(f"http://ip-api.com/json/{ip}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.0) as res:
            data = json.loads(res.read().decode("utf-8"))
            if data.get("status") == "success":
                city = data.get("city", "")
                region = data.get("regionName", "")
                country = data.get("country", "")
                loc_parts = []
                if city:
                    loc_parts.append(city)
                if region and region != city:
                    loc_parts.append(region)
                if country:
                    loc_parts.append("Việt Nam" if country == "Vietnam" else country)
                if loc_parts:
                    location_str = "📍 " + ", ".join(loc_parts)
    except Exception:
        pass

    _IP_LOCATION_CACHE[ip] = location_str
    return location_str

@app.before_request
def track_visitor():
    if request.path.startswith("/static") or request.path.startswith("/admin") or request.path in ["/robots.txt", "/sitemap.xml"]:
        return
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip:
            ip = ip.split(",")[0].strip()
        user_agent_str = request.user_agent.string if request.user_agent else ""
        visit = PageVisit(
            user_id=session.get("user_id"),
            ip_address=ip[:50] if ip else "127.0.0.1",
            location=get_location_name(ip),
            device_type=get_device_type(user_agent_str),
            user_agent=user_agent_str[:250],
            path=request.path[:250]
        )
        db.session.add(visit)
        db.session.commit()
    except Exception:
        db.session.rollback()

@app.after_request
def prevent_private_pages_from_being_indexed(response):
    private_endpoints = {
        "login", "register", "logout", "cart", "cart_add", "cart_update", "cart_remove",
        "checkout", "payment", "payment_notify_paid", "account_orders", "cad_download", "admin_dashboard", "admin_products",
        "admin_product_add", "admin_product_delete", "admin_orders", "admin_order_status",
        "admin_contacts",
    }
    if request.endpoint in private_endpoints:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Vui lòng đăng nhập tài khoản quản trị.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper

def cart_items():
    cart = session.get("cart", {})
    result, total = [], 0
    for pid, qty in cart.items():
        p = db.session.get(Product, int(pid))
        if p:
            qty = max(1, int(qty))
            subtotal = p.price * qty
            result.append((p, qty, subtotal))
            total += subtotal
    return result, total

@app.context_processor
def inject_globals():
    items, total = cart_items()
    return {
        "cart_count": sum(x[1] for x in items),
        "cart_total": total,
        "site_url": app.config["SITE_URL"] or request.url_root.rstrip("/"),
        "google_site_verification": app.config["GOOGLE_SITE_VERIFICATION"],
        "bank_id": app.config.get("BANK_ID", "VCB"),
        "bank_account": app.config.get("BANK_ACCOUNT", "1016703323"),
        "bank_account_name": app.config.get("BANK_ACCOUNT_NAME", "TRINH DUC NHIEM"),
        "bank_branch": app.config.get("BANK_BRANCH", "PGD Ea Kar"),
    }

@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all()
        if not Category.query.first():
            names = ["Thiết bị điện", "Thiết bị tự động hóa", "Tủ điện & phụ kiện", "Thư viện AutoCAD", "Tài liệu kỹ thuật"]
            for n in names:
                db.session.add(Category(name=n, slug=n.lower().replace(" ", "-").replace("&","and")))
        if not User.query.filter_by(email=app.config["ADMIN_EMAIL"]).first():
            db.session.add(User(name="Administrator", email=app.config["ADMIN_EMAIL"],
                                password_hash=generate_password_hash(app.config["ADMIN_PASSWORD"]), is_admin=True))
        db.session.commit()
        print("Database initialized.")

@app.route("/")
def index():
    categories = Category.query.order_by(Category.id.asc()).all()
    
    # 3 Main Pillars
    cat_autocad = Category.query.filter_by(slug="thu-vien-autocad").first()
    cat_panel = Category.query.filter_by(slug="thiet-ke-tu-dien").first()
    cat_automation = Category.query.filter_by(slug="do-an-tu-dong-hoa").first()
    
    cad_products = Product.query.filter_by(category_id=cat_autocad.id, status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all() if cat_autocad else []
    panel_products = Product.query.filter_by(category_id=cat_panel.id, status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all() if cat_panel else []
    automation_products = Product.query.filter_by(category_id=cat_automation.id, status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all() if cat_automation else []
    
    return render_template("index.html",
                           categories=categories,
                           cat_autocad=cat_autocad,
                           cat_panel=cat_panel,
                           cat_automation=cat_automation,
                           cad_products=cad_products,
                           panel_products=panel_products,
                           automation_products=automation_products)

@app.route("/thu-vien-autocad")
def category_autocad():
    return redirect(url_for("products", category="thu-vien-autocad"))

@app.route("/thiet-ke-tu-dien")
def category_panel():
    return redirect(url_for("products", category="thiet-ke-tu-dien"))

@app.route("/do-an-tu-dong-hoa")
def category_automation():
    return redirect(url_for("products", category="do-an-tu-dong-hoa"))

@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    query = Product.query.filter_by(status="active")
    if q:
        pattern = f"%{q}%"
        query = query.outerjoin(Category).filter(or_(
            Product.name.ilike(pattern), Product.sku.ilike(pattern),
            Product.description.ilike(pattern), Product.specifications.ilike(pattern),
            Category.name.ilike(pattern),
        ))
    if category:
        if not q:
            query = query.join(Category)
        query = query.filter(Category.slug == category)
    products = query.distinct().order_by(Product.sort_order.asc(), Product.id.asc()).all()
    return render_template("products.html", products=products, categories=Category.query.all(), q=q,
                           selected_category=category, canonical_url=public_url("products"))

@app.route("/product/<slug>")
def product_detail(slug):
    product = Product.query.filter_by(slug=slug, status="active").first_or_404()
    description = (product.description or f"Thông tin, giá và báo giá {product.name} tại Nhiệm Autotech.").strip()
    return render_template("product_detail.html", product=product, seo_description=description[:160],
                           canonical_url=public_url("product_detail", slug=product.slug))

@app.route("/cad-library")
def cad_library():
    products = Product.query.filter(Product.cad_file.isnot(None), Product.status=="active").order_by(Product.created_at.desc()).all()
    return render_template("cad_library.html", products=products)

@app.route("/library-by-brand")
def library_by_brand():
    return render_template("library_by_brand.html")

@app.route("/thuvien-autocad-thiet-bi-dien")
def keyword_autocad():
    return render_template("keyword_autocad.html")

@app.route("/mach-dieu-khien-plc")
def keyword_plc():
    return render_template("keyword_plc.html")

@app.route("/panel-forms")
def panel_forms():
    return render_template("panel_forms.html")

@app.route("/ping")
def ping():
    return Response("OK", mimetype="text/plain")

@app.route("/google79bc38910d6f203a.html")
def google_verify_file():
    return Response("google-site-verification: google79bc38910d6f203a.html", mimetype="text/html")

@app.route("/robots.txt")
def robots_txt():
    base_url = app.config["SITE_URL"] or request.url_root.rstrip("/")
    content = f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n"
    return Response(content, mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap_xml():
    base_url = app.config["SITE_URL"] or request.url_root.rstrip("/")
    static_pages = [
        ("", "daily", "1.0"),
        ("/products", "daily", "0.9"),
        ("/thu-vien-autocad", "daily", "0.9"),
        ("/thiet-ke-tu-dien", "daily", "0.9"),
        ("/do-an-tu-dong-hoa", "daily", "0.9"),
        ("/thuvien-autocad-thiet-bi-dien", "weekly", "0.85"),
        ("/panel-forms", "weekly", "0.85"),
        ("/mach-dieu-khien-plc", "weekly", "0.85"),
        ("/library-by-brand", "weekly", "0.85"),
        ("/cad-library", "weekly", "0.85"),
        ("/quote", "monthly", "0.7"),
        ("/contact", "monthly", "0.7"),
        ("/tra-cuu-don-hang", "weekly", "0.6"),
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for path, freq, prio in static_pages:
        xml += "  <url>\n"
        xml += f"    <loc>{escape(base_url + path)}</loc>\n"
        xml += f"    <changefreq>{freq}</changefreq>\n"
        xml += f"    <priority>{prio}</priority>\n"
        xml += "  </url>\n"
    for product in Product.query.filter_by(status="active").all():
        lastmod = product.created_at.replace(tzinfo=timezone.utc).date().isoformat()
        xml += "  <url>\n"
        xml += f"    <loc>{escape(base_url + '/product/' + url_quote(product.slug))}</loc>\n"
        xml += f"    <lastmod>{lastmod}</lastmod>\n"
        xml += "    <changefreq>weekly</changefreq>\n"
        xml += "    <priority>0.8</priority>\n"
        xml += "  </url>\n"
    xml += "</urlset>\n"
    return Response(xml, mimetype="application/xml")

@app.route("/buy-now/<int:product_id>", methods=["GET", "POST"])
def buy_now(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.status != "active":
        abort(404)
    # Set cart to this single product with qty 1
    session["cart"] = {str(product_id): 1}
    session.modified = True
    return redirect(url_for("checkout"))

@app.route("/cad-download/<int:product_id>")
def cad_download(product_id):
    product = db.session.get(Product, product_id)
    if not product or not product.cad_file:
        abort(404)
    
    # Cho phép Admin tải file trực tiếp
    if session.get("is_admin"):
        path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
        file_path = path / product.cad_file
        if not file_path.exists():
            abort(404)
        return send_from_directory(path, product.cad_file, as_attachment=True)

    user_id = session.get("user_id")
    order_id = request.args.get("order_id", type=int)

    # Check if this customer or order has paid for this product
    purchase = None
    if user_id:
        purchase = (Order.query.join(OrderItem)
                    .filter(Order.user_id == user_id, Order.status == "paid",
                            OrderItem.product_id == product.id)
                    .first())
    if not purchase and order_id:
        order = db.session.get(Order, order_id)
        if order and order.status == "paid":
            for item in order.items:
                if item.product_id == product.id:
                    purchase = order
                    break

    if not purchase:
        flash("Bạn chỉ có thể tải file sau khi đơn hàng đã được Admin xác nhận thanh toán.", "warning")
        return redirect(url_for("payment", order_id=order_id) if order_id else url_for("order_lookup"))

    path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
    file_path = path / product.cad_file
    if not file_path.exists():
        abort(404)
    if user_id:
        db.session.add(Download(user_id=user_id, product_id=product.id))
        db.session.commit()
    return send_from_directory(path, product.cad_file, as_attachment=True)

@app.route("/cart")
def cart():
    items, total = cart_items()
    return render_template("cart.html", items=items, total=total)

@app.post("/cart/add/<int:product_id>")
def cart_add(product_id):
    product = db.session.get(Product, product_id)
    if not product or product.status != "active":
        abort(404)
    cart = session.setdefault("cart", {})
    key = str(product_id)
    cart[key] = int(cart.get(key, 0)) + 1
    session.modified = True
    flash(f"Đã thêm '{product.name}' vào giỏ hàng.", "success")
    return redirect(request.referrer or url_for("products"))

@app.post("/cart/update")
def cart_update():
    cart = {}
    for key, value in request.form.items():
        if key.startswith("qty_"):
            pid = key[4:]
            try:
                qty = int(value)
                if qty > 0:
                    cart[pid] = min(qty, 999)
            except ValueError:
                pass
    session["cart"] = cart
    flash("Đã cập nhật giỏ hàng.", "success")
    return redirect(url_for("cart"))

@app.post("/cart/remove/<int:product_id>")
def cart_remove(product_id):
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart
    return redirect(url_for("cart"))

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    items, total = cart_items()
    if not items:
        flash("Giỏ hàng của bạn đang trống. Vui lòng chọn sản phẩm cần mua.", "info")
        return redirect(url_for("products"))

    current_user = db.session.get(User, session["user_id"]) if session.get("user_id") else None

    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        note = request.form.get("note", "").strip()
        address = request.form.get("address", "Tài liệu số / File CAD").strip()

        if not customer_name or not email or not phone:
            flash("Vui lòng điền đầy đủ: Họ tên, Gmail nhận file CAD và Số điện thoại.", "warning")
            return render_template("checkout.html", items=items, total=total, user=current_user)

        # Create or link user account automatically
        user = current_user
        if not user:
            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(
                    name=customer_name,
                    email=email,
                    password_hash=generate_password_hash(phone or "Nhiem@123456"),
                    is_admin=False
                )
                db.session.add(user)
                db.session.commit()
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin

        # Create order
        order = Order(
            user_id=user.id,
            customer_name=customer_name,
            email=email,
            phone=phone,
            address=address or "Tài liệu số / File CAD",
            note=note,
            total=total,
            status="pending_payment"
        )
        db.session.add(order)
        db.session.flush()

        for p, qty, subtotal in items:
            db.session.add(OrderItem(
                order_id=order.id,
                product_id=p.id,
                product_name=p.name,
                price=p.price,
                quantity=qty,
                subtotal=subtotal
            ))
        db.session.commit()

        # Clear cart
        session["cart"] = {}
        flash(f"Tạo đơn hàng #{order.id} thành công! Quý khách vui lòng quét mã QR Vietcombank để thanh toán.", "success")
        return redirect(url_for("payment", order_id=order.id))

    return render_template("checkout.html", items=items, total=total, user=current_user)

@app.route("/payment/<int:order_id>")
def payment(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)

    transfer_note = f"NA{order.id}"
    qr_url = None
    bank_id = app.config.get("BANK_ID", "VCB")
    bank_account = app.config.get("BANK_ACCOUNT", "1016703323")
    bank_account_name = app.config.get("BANK_ACCOUNT_NAME", "TRINH DUC NHIEM")
    bank_branch = app.config.get("BANK_BRANCH", "PGD Ea Kar")

    if bank_id and bank_account:
        parameters = urlencode({
            "amount": f"{order.total:.0f}",
            "addInfo": transfer_note,
            "accountName": bank_account_name,
        })
        qr_url = f"https://img.vietqr.io/image/{url_quote(bank_id)}-{url_quote(bank_account)}-compact2.png?{parameters}"
    
    customer_email = order.email or (order.user.email if order.user else "")
    return render_template("payment.html", order=order, transfer_note=transfer_note, qr_url=qr_url,
                           bank_id=bank_id, bank_account=bank_account,
                           bank_account_name=bank_account_name, bank_branch=bank_branch,
                           user_email=customer_email)

@app.post("/payment/<int:order_id>/notify-paid")
def payment_notify_paid(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    if order.status == "pending_payment":
        order.status = "awaiting_confirmation"
        db.session.commit()
        customer_email = order.email or (order.user.email if order.user else "")
        flash(f"Đã ghi nhận thông báo chuyển khoản đơn #{order.id}. Hệ thống đang chờ Admin xác nhận. Sau khi duyệt, file CAD sẽ được gửi tự động về Gmail: {customer_email}", "success")
    elif order.status == "awaiting_confirmation":
        flash(f"Đơn hàng #{order.id} đang trong trạng thái chờ Admin xác nhận.", "info")
    elif order.status == "paid":
        flash(f"Đơn hàng #{order.id} đã được xác nhận thanh toán thành công!", "success")
    return redirect(url_for("payment", order_id=order.id))

@app.route("/tra-cuu-don-hang", methods=["GET", "POST"])
def order_lookup():
    orders = None
    q = request.form.get("q", "").strip() if request.method == "POST" else request.args.get("q", "").strip()
    if q:
        query = Order.query
        if q.isdigit():
            orders = query.filter(or_(Order.id == int(q), Order.phone == q)).order_by(Order.created_at.desc()).all()
        else:
            orders = query.filter(or_(Order.email.ilike(f"%{q}%"), Order.phone.ilike(f"%{q}%"))).order_by(Order.created_at.desc()).all()
    return render_template("order_lookup.html", orders=orders, q=q)

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        db.session.add(Contact(name=request.form["name"], email=request.form["email"],
                               phone=request.form.get("phone",""), message=request.form["message"]))
        db.session.commit()
        flash("Đã gửi liên hệ. Nhiệm sẽ phản hồi sớm.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")

@app.route("/quote", methods=["GET", "POST"])
def quote():
    default_product = request.args.get("product", "").strip()
    if request.method == "POST":
        product_name = request.form.get("product_name", "").strip()
        quantity = request.form.get("quantity", "").strip()
        requirements = request.form.get("message", "").strip()
        message = (
            f"Sản phẩm quan tâm: {product_name}\n"
            f"Số lượng: {quantity}\n"
            f"Yêu cầu: {requirements}"
        )
        db.session.add(Contact(
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            message=message,
        ))
        db.session.commit()
        flash("Yêu cầu báo giá của bạn đã được gửi thành công. Nhiệm sẽ liên hệ trong thời gian sớm nhất.", "success")
        return redirect(url_for("quote"))
    return render_template("quote.html", default_product=default_product)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].lower().strip()).first()
        if user and check_password_hash(user.password_hash, request.form["password"]):
            session["user_id"], session["is_admin"] = user.id, user.is_admin
            return redirect(request.args.get("next") or url_for("index"))
        flash("Email hoặc mật khẩu không đúng.", "danger")
    return render_template("auth/login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        if User.query.filter_by(email=email).first():
            flash("Email đã tồn tại.", "danger")
            return redirect(url_for("register"))
        user = User(name=request.form["name"], email=email,
                    password_hash=generate_password_hash(request.form["password"]))
        db.session.add(user); db.session.commit()
        flash("Tạo tài khoản thành công. Hãy đăng nhập.", "success")
        return redirect(url_for("login"))
    return render_template("auth/register.html")

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/account/orders")
def account_orders():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    orders = Order.query.filter_by(user_id=session["user_id"]).order_by(Order.created_at.desc()).all()
    return render_template("account/orders.html", orders=orders)

# ADMIN
@app.route("/admin")
@admin_required
def admin_dashboard():
    cat_autocad = Category.query.filter_by(slug="thu-vien-autocad").first()
    cat_panel = Category.query.filter_by(slug="thiet-ke-tu-dien").first()
    cat_automation = Category.query.filter_by(slug="do-an-tu-dong-hoa").first()

    cad_count = Product.query.filter_by(category_id=cat_autocad.id).count() if cat_autocad else 0
    panel_count = Product.query.filter_by(category_id=cat_panel.id).count() if cat_panel else 0
    automation_count = Product.query.filter_by(category_id=cat_automation.id).count() if cat_automation else 0

    from datetime import datetime, date, time
    today_start = datetime.combine(date.today(), time.min)

    total_visits = PageVisit.query.count()
    today_visits = PageVisit.query.filter(PageVisit.created_at >= today_start).count()
    unique_today = db.session.query(PageVisit.ip_address).filter(PageVisit.created_at >= today_start).distinct().count()
    recent_visits = PageVisit.query.order_by(PageVisit.created_at.desc()).limit(15).all()

    return render_template("admin/dashboard.html",
                           product_count=Product.query.count(),
                           cad_count=cad_count,
                           panel_count=panel_count,
                           automation_count=automation_count,
                           order_count=Order.query.count(),
                           awaiting_order_count=Order.query.filter_by(status="awaiting_confirmation").count(),
                           user_count=User.query.count(),
                           contact_count=Contact.query.filter_by(is_read=False).count(),
                           total_visits=total_visits,
                           today_visits=today_visits,
                           unique_today=unique_today,
                           recent_visits=recent_visits)

@app.route("/admin/products")
@admin_required
def admin_products():
    category_slug = request.args.get("category", "").strip()
    query = Product.query
    if category_slug:
        cat = Category.query.filter_by(slug=category_slug).first()
        if cat:
            query = query.filter_by(category_id=cat.id)
    products = query.order_by(Product.created_at.desc()).all()
    categories = Category.query.all()
    return render_template("admin/products.html", products=products, categories=categories, selected_category=category_slug)

@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    if request.method == "POST":
        category = db.session.get(Category, request.form.get("category_id", type=int))
        image = request.files.get("image")
        if not category or not image or not image.filename:
            flash("Vui lòng chọn ảnh cho danh mục.", "warning")
            return redirect(url_for("admin_categories"))
        ext = Path(secure_filename(image.filename)).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            flash("Chỉ hỗ trợ ảnh JPG, PNG, WEBP hoặc GIF.", "danger")
            return redirect(url_for("admin_categories"))
        filename = f"category-{category.id}{ext}"
        image_path = Path(app.config["UPLOAD_FOLDER"]) / "images"
        image_path.mkdir(parents=True, exist_ok=True)
        image.save(image_path / filename)
        category.image = filename
        db.session.commit()
        flash("Đã cập nhật ảnh danh mục.", "success")
        return redirect(url_for("admin_categories"))
    return render_template("admin/categories.html", categories=Category.query.order_by(Category.name).all())

def save_product_image(fobj, prefix="img"):
    if not fobj or not fobj.filename:
        return None
    orig_name = fobj.filename.strip()
    ext = Path(orig_name).suffix.lower()
    if not ext or ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
        ext = ".jpg"
    import uuid, time, re
    clean_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '-', prefix).strip('-') or 'img'
    fname = f"{clean_prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}{ext}"
    img_dir = Path(app.config["UPLOAD_FOLDER"]) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    fobj.save(img_dir / fname)
    return fname

@app.route("/admin/products/add", methods=["GET","POST"])
@admin_required
def admin_product_add():
    categories = Category.query.all()
    if request.method == "POST":
        name = request.form["name"].strip()
        slug = request.form.get("slug", "").strip().lower()
        if not slug:
            import re
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        price = float(request.form.get("price", 0) or 0)
        product = Product(name=name, slug=slug, sku=request.form.get("sku",""),
                          description=request.form.get("description",""),
                          specifications=request.form.get("specifications",""),
                          price=price, stock=int(request.form.get("stock",999) or 999),
                          category_id=int(request.form["category_id"]),
                          video_url=request.form.get("video_url","").strip())
        
        # 1. Main image
        main_img = request.files.get("image") or request.files.get("image_1")
        if main_img and main_img.filename:
            product.image = save_product_image(main_img, f"{slug}-main")
            
        # 2. Additional individual image slots (image_2, image_3, image_4, image_5)
        extra_imgs = []
        for slot in ["image_2", "image_3", "image_4", "image_5"]:
            fslot = request.files.get(slot)
            if fslot and fslot.filename:
                saved = save_product_image(fslot, f"{slug}-{slot}")
                if saved:
                    extra_imgs.append(saved)
                    
        # 3. Bulk multiple images
        gallery_files = request.files.getlist("gallery_images")
        for idx, gfile in enumerate(gallery_files):
            if gfile and gfile.filename:
                saved = save_product_image(gfile, f"{slug}-gal-{idx+1}")
                if saved:
                    extra_imgs.append(saved)
                    
        if extra_imgs:
            if not product.image and len(extra_imgs) > 0:
                product.image = extra_imgs.pop(0)
            product.images = ",".join(extra_imgs)

        # 4. Video file
        video = request.files.get("video_file")
        if video and video.filename:
            ext = Path(video.filename).suffix.lower().lstrip(".")
            if ext in ALLOWED_VIDEOS:
                import time, uuid
                v_filename = f"{slug}-video-{int(time.time())}.{ext}"
                video_path = Path(app.config["UPLOAD_FOLDER"]) / "videos"
                video_path.mkdir(parents=True, exist_ok=True)
                video.save(video_path / v_filename)
                product.video_file = v_filename

        # 5. CAD file
        cad = request.files.get("cad_file")
        if cad and cad.filename:
            ext = Path(cad.filename).suffix.lower().lstrip(".")
            if ext not in ALLOWED_DOCS:
                flash(f"Định dạng file .{ext} chưa được hỗ trợ. Hãy nén dạng .zip hoặc .rar hoặc .dwg", "danger")
                return render_template("admin/product_add.html", categories=categories)
            filename = f"{slug}-{secure_filename(cad.filename)}"
            cad_path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
            cad_path.mkdir(parents=True, exist_ok=True)
            cad.save(cad_path / filename)
            product.cad_file = filename
            
        db.session.add(product)
        db.session.commit()
        flash(f"Đã đăng '{product.name}' thành công lên danh mục!", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_add.html", categories=categories)

@app.route("/admin/products/edit/<int:product_id>", methods=["GET","POST"])
@admin_required
def admin_product_edit(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    categories = Category.query.all()
    if request.method == "POST":
        product.name = request.form["name"].strip()
        slug = request.form.get("slug", "").strip().lower()
        if slug:
            product.slug = slug
        product.sku = request.form.get("sku","").strip()
        product.price = float(request.form.get("price", 0) or 0)
        product.stock = int(request.form.get("stock", 999) or 999)
        product.category_id = int(request.form["category_id"])
        product.description = request.form.get("description","").strip()
        product.specifications = request.form.get("specifications","").strip()
        product.status = request.form.get("status", "active")
        product.video_url = request.form.get("video_url", "").strip()
        
        # 1. Main image update
        main_img = request.files.get("image") or request.files.get("image_1")
        if main_img and main_img.filename:
            saved = save_product_image(main_img, f"{product.slug}-main")
            if saved:
                product.image = saved
            
        # 2. Existing extra images list
        existing_images = [img.strip() for img in (product.images or "").split(",") if img.strip()]
        
        # 3. Individual extra slots (image_2, image_3, image_4, image_5)
        for slot in ["image_2", "image_3", "image_4", "image_5"]:
            fslot = request.files.get(slot)
            if fslot and fslot.filename:
                saved = save_product_image(fslot, f"{product.slug}-{slot}")
                if saved:
                    existing_images.append(saved)

        # 4. Multiple extra files
        gallery_files = request.files.getlist("gallery_images")
        for idx, gfile in enumerate(gallery_files):
            if gfile and gfile.filename:
                saved = save_product_image(gfile, f"{product.slug}-gal-{idx+1}")
                if saved:
                    existing_images.append(saved)
                    
        # Remove duplicates while preserving order
        dedup_images = []
        for img in existing_images:
            if img and img not in dedup_images and img != product.image:
                dedup_images.append(img)
        product.images = ",".join(dedup_images)

        # 5. Video update
        video = request.files.get("video_file")
        if video and video.filename:
            ext = Path(video.filename).suffix.lower().lstrip(".")
            if ext in ALLOWED_VIDEOS:
                import time
                v_filename = f"{product.slug}-video-{int(time.time())}.{ext}"
                video_path = Path(app.config["UPLOAD_FOLDER"]) / "videos"
                video_path.mkdir(parents=True, exist_ok=True)
                video.save(video_path / v_filename)
                product.video_file = v_filename

        # 6. CAD file update
        cad = request.files.get("cad_file")
        if cad and cad.filename:
            ext = Path(cad.filename).suffix.lower().lstrip(".")
            if ext not in ALLOWED_DOCS:
                flash(f"Định dạng file .{ext} chưa được hỗ trợ.", "danger")
                return render_template("admin/product_edit.html", product=product, categories=categories)
            filename = f"{product.slug}-{secure_filename(cad.filename)}"
            cad_path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
            cad_path.mkdir(parents=True, exist_ok=True)
            cad.save(cad_path / filename)
            product.cad_file = filename
            
        db.session.commit()
        flash(f"Đã cập nhật '{product.name}' thành công!", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_edit.html", product=product, categories=categories)

@app.route("/admin/products/<int:product_id>/delete-video", methods=["GET", "POST"])
@admin_required
def admin_product_delete_video(product_id):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    product.video_file = None
    db.session.commit()
    flash("Đã xóa video tải lên của sản phẩm.", "info")
    return redirect(url_for("admin_product_edit", product_id=product.id))

@app.route("/admin/products/<int:product_id>/delete-image/<path:img_name>", methods=["GET", "POST"])
@admin_required
def admin_product_delete_image(product_id, img_name):
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    if product.image == img_name:
        product.image = None
    existing_images = [img.strip() for img in (product.images or "").split(",") if img.strip()]
    if img_name in existing_images:
        existing_images.remove(img_name)
    # If main image was deleted, promote first extra image as main
    if not product.image and len(existing_images) > 0:
        product.image = existing_images.pop(0)
    product.images = ",".join(existing_images)
    db.session.commit()
    flash(f"Đã xóa ảnh khỏi sản phẩm thành công.", "info")
    return redirect(url_for("admin_product_edit", product_id=product.id))

@app.post("/admin/products/toggle/<int:product_id>")
@admin_required
def admin_product_toggle(product_id):
    p = db.session.get(Product, product_id)
    if p:
        p.status = "hidden" if p.status == "active" else "active"
        db.session.commit()
        flash(f"Đã chuyển trạng thái sản phẩm #{p.id} thành '{p.status}'.", "info")
    return redirect(url_for("admin_products"))

@app.post("/admin/products/delete/<int:product_id>")
@admin_required
def admin_product_delete(product_id):
    p = db.session.get(Product, product_id)
    if p:
        db.session.delete(p)
        db.session.commit()
        flash("Đã xóa sản phẩm khỏi hệ thống.", "info")
    return redirect(url_for("admin_products"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    return render_template("admin/orders.html", orders=Order.query.order_by(Order.created_at.desc()).all())

@app.post("/admin/orders/<int:order_id>/status")
@admin_required
def admin_order_status(order_id):
    order = db.session.get(Order, order_id)
    allowed_statuses = {"pending_payment", "awaiting_confirmation", "paid", "cancelled"}
    status = request.form.get("status")
    if not order or status not in allowed_statuses:
        abort(400)
    old_status = order.status
    order.status = status
    db.session.commit()

    if status == "paid" and old_status != "paid":
        sent = send_order_paid_email(order)
        customer_email = order.email or (order.user.email if order.user else "")
        if sent:
            flash(f"Đã duyệt đơn #{order.id} là ĐÃ THANH TOÁN. Đã tự động gửi email và file CAD về Gmail: {customer_email}", "success")
        else:
            flash(f"Đã duyệt đơn #{order.id} là ĐÃ THANH TOÁN. Quyền tải file CAD trên website đã được kích hoạt cho khách ({customer_email}).", "success")
    else:
        status_labels = {
            "pending_payment": "Chờ thanh toán",
            "awaiting_confirmation": "Khách đã báo thanh toán (Chờ duyệt)",
            "paid": "Đã thanh toán",
            "cancelled": "Đã hủy"
        }
        flash(f"Đã cập nhật trạng thái đơn #{order.id} thành '{status_labels.get(status, status)}'.", "info")
    return redirect(url_for("admin_orders"))

@app.route("/admin/contacts")
@admin_required
def admin_contacts():
    contacts = Contact.query.order_by(Contact.created_at.desc()).all()
    return render_template("admin/contacts.html", contacts=contacts)

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    admin_user = db.session.get(User, session["user_id"]) if session.get("user_id") else None
    if not admin_user:
        admin_user = User.query.filter_by(is_admin=True).first()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not name or not email:
            flash("Vui lòng nhập đầy đủ Tên và Email quản trị.", "warning")
            return render_template("admin/settings.html", admin_user=admin_user)

        if email != admin_user.email and User.query.filter_by(email=email).first():
            flash("Email đăng nhập này đã được sử dụng bởi tài khoản khác.", "danger")
            return render_template("admin/settings.html", admin_user=admin_user)

        admin_user.name = name
        admin_user.email = email

        if new_password:
            if not current_password or not check_password_hash(admin_user.password_hash, current_password):
                flash("Mật khẩu hiện tại không chính xác.", "danger")
                return render_template("admin/settings.html", admin_user=admin_user)
            if len(new_password) < 6:
                flash("Mật khẩu mới phải có ít nhất 6 ký tự.", "warning")
                return render_template("admin/settings.html", admin_user=admin_user)
            if new_password != confirm_password:
                flash("Mật khẩu mới và mật khẩu xác nhận không khớp.", "danger")
                return render_template("admin/settings.html", admin_user=admin_user)
            
            admin_user.password_hash = generate_password_hash(new_password)
            flash("Đã cập nhật Tên, Email và ĐỔI MẬT KHẨU Admin thành công!", "success")
        else:
            flash("Đã cập nhật Tên và Email Admin thành công!", "success")

        db.session.commit()
        return redirect(url_for("admin_settings"))

    return render_template("admin/settings.html", admin_user=admin_user)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
