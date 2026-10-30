from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_, and_, inspect, text
from models import db, User, Category, Product, Order, OrderItem, Contact, Download, PageVisit, Notification, ChatMessage, AdminIP, CloudAsset
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

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

BOT_IP_PREFIXES = (
    "66.249.", "64.233.", "72.14.", "209.85.", "216.58.", "172.217.",  # Google & Googlebot Crawlers
    "54.81.", "142.147.", "157.245.", "52.", "54.", "3.8", "3.9",      # Datacenter automated scanners
    "69.171.", "173.252.", "157.240.", "31.13.",
    "35.197.", "35.199.", "35.227.", "35.230.", "34.",
    "57.141.", "104.248.", "194.59.", "147.235.", "162.120.", "138.226.",
    "165.225.", "216.227.", "68.167.", "170.23.", "191.31.", "166.0.",
    "79.177.", "185.47.", "9.129.", "15.235.", "91.176.", "5.62.", "104.28.",
    "103.59.", "162.216."
)

SCANNER_PATH_PATTERNS = (
    "/wp-", "wp-content", "wp-includes", "wp-admin", "xmlrpc", "wlwmanifest",
    ".php", ".env", ".git", ".well-known", "cgi-bin", "phpmyadmin", "config.",
    "/feed", "/rss", "actuator", "/owa", "/autodiscover", "/vendor", "/telescope",
    "/install", "/setup", "/backup", "/dump", "/eval-stdin", "/shell", "/hnap1",
    "/boaform", "/dns-query", "/stalker_portal", "/ipfs/", "/asset-manifest.json",
    "webdav", "invoker", "jmx-console"
)

def save_file_to_cloud(filename, file_bytes, asset_type="image", mime_type=None):
    """Save or update file binary to CloudAsset table in Supabase PostgreSQL."""
    if not filename or not file_bytes:
        return None
    try:
        import mimetypes
        if not mime_type:
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type = "application/octet-stream"
        
        # Hỗ trợ lưu file ảnh, tài liệu CAD và video dung lượng lên tới 100MB vào Đám mây Supabase
        if len(file_bytes) > 100 * 1024 * 1024:
            print(f"[CLOUD ASSET WARN] File '{filename}' ({len(file_bytes)} bytes) > 100MB. Saved to local disk.")
            return None

        asset = CloudAsset.query.filter_by(filename=filename).first()
        if not asset:
            asset = CloudAsset(
                filename=filename,
                asset_type=asset_type,
                mime_type=mime_type,
                file_size=len(file_bytes),
                data=file_bytes
            )
            db.session.add(asset)
        else:
            asset.data = file_bytes
            asset.file_size = len(file_bytes)
            asset.asset_type = asset_type
            asset.mime_type = mime_type
        db.session.commit()
        print(f"[CLOUD ASSET] Saved '{filename}' ({len(file_bytes)} bytes) to Supabase Cloud.")
        return asset
    except Exception as e:
        db.session.rollback()
        print(f"[CLOUD ASSET ERROR] Could not save '{filename}' to Supabase: {e}")
        return None

def restore_all_cloud_assets_to_local():
    """Tự động kéo toàn bộ ảnh, video và file CAD từ CloudAsset (Supabase) về ổ đĩa cục bộ static/uploads/ ngay khi khởi động.
    Đảm bảo mỗi khi cập nhật code mới hoặc Render dựng container mới thì 100% ảnh và file đã có sẵn ngay lập tức!"""
    try:
        upload_base = Path(app.config["UPLOAD_FOLDER"])
        folder_map = {"image": "images", "video": "videos", "cad": "cad"}
        
        # Chỉ truy vấn siêu dữ liệu (id, filename, asset_type) trước để kiểm tra siêu tốc (<0.1s)
        cloud_assets_meta = db.session.query(CloudAsset.id, CloudAsset.filename, CloudAsset.asset_type).all()
        missing_items = []
        for aid, fname, atype in cloud_assets_meta:
            folder = folder_map.get(atype, "images")
            target_dir = upload_base / folder
            target_file = target_dir / fname
            if not target_file.exists():
                missing_items.append((aid, fname, folder))
                
        if not missing_items:
            print(f"[CLOUD RESTORE] Ready: All {len(cloud_assets_meta)} files from CloudAsset verified on disk.")
            return
            
        print(f"[CLOUD RESTORE] Syncing {len(missing_items)} missing files from Supabase Cloud to disk...")
        restored = 0
        for aid, fname, folder in missing_items:
            try:
                asset = db.session.get(CloudAsset, aid)
                if asset and asset.data:
                    target_dir = upload_base / folder
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_file = target_dir / fname
                    target_file.write_bytes(asset.data)
                    restored += 1
            except Exception as fe:
                print(f"[CLOUD RESTORE WARN] Could not restore '{fname}': {fe}")
                
        print(f"[CLOUD RESTORE] Successfully restored {restored}/{len(missing_items)} files from Supabase Cloud.")
    except Exception as e:
        print(f"[CLOUD RESTORE ERROR] Exception restoring assets: {e}")

def sync_local_uploads_to_cloud():
    """Scan local uploads and ensure any missing file in CloudAsset is automatically synced to Supabase."""
    try:
        import mimetypes
        existing_filenames = {row[0] for row in db.session.query(CloudAsset.filename).all()}
        upload_base = Path(app.config["UPLOAD_FOLDER"])
        synced_count = 0

        for folder, asset_type in [("images", "image"), ("videos", "video"), ("cad", "cad")]:
            target_dir = upload_base / folder
            if not target_dir.exists():
                continue
            for fpath in target_dir.iterdir():
                if fpath.is_file() and not fpath.name.startswith("."):
                    fname = fpath.name
                    if fname not in existing_filenames:
                        try:
                            f_bytes = fpath.read_bytes()
                            if len(f_bytes) <= 100 * 1024 * 1024:
                                m_type, _ = mimetypes.guess_type(fname)
                                asset = CloudAsset(
                                    filename=fname,
                                    asset_type=asset_type,
                                    mime_type=m_type or "application/octet-stream",
                                    file_size=len(f_bytes),
                                    data=f_bytes
                                )
                                db.session.add(asset)
                                existing_filenames.add(fname)
                                synced_count += 1
                        except Exception as fe:
                            print(f"[CLOUD SYNC WARN] Could not read {fname}: {fe}")

        if synced_count > 0:
            db.session.commit()
            print(f"[CLOUD ASSET SYNC] Auto-synced {synced_count} new local files to Supabase Cloud.")
    except Exception as e:
        db.session.rollback()
        print(f"[CLOUD ASSET SYNC ERROR] {e}")

def initialize_database():
    """Create and safely upgrade the database on startup."""
    db.create_all()
    try:
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
            if "duration_seconds" not in columns_visit:
                db.session.execute(text("ALTER TABLE page_visit ADD COLUMN duration_seconds INTEGER DEFAULT 0"))
            if "referrer" not in columns_visit:
                db.session.execute(text("ALTER TABLE page_visit ADD COLUMN referrer VARCHAR(350) DEFAULT ''"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[DB UPGRADE NOTE] {e}")
        
    default_categories = [
        ("Thư viện AutoCAD", "thu-vien-autocad"),
        ("Thiết kế Tủ điện & Mạch điều khiển", "thiet-ke-tu-dien"),
        ("Mô hình & Đồ án Tự động hóa", "do-an-tu-dong-hoa"),
    ]
    for name, slug in default_categories:
        cat_obj = Category.query.filter_by(slug=slug).first()
        if not cat_obj:
            db.session.add(Category(name=name, slug=slug))
        elif slug == "thiet-ke-tu-dien" and "Khóa học" in cat_obj.name:
            cat_obj.name = "Thiết kế Tủ điện & Mạch điều khiển"
    if not User.query.filter_by(email=app.config["ADMIN_EMAIL"]).first():
        db.session.add(User(name="Administrator", email=app.config["ADMIN_EMAIL"],
                            password_hash=generate_password_hash(app.config["ADMIN_PASSWORD"]), is_admin=True))
    db.session.commit()
    
    json_path = Path(__file__).resolve().parent / "products_dump.json"
    if json_path.exists() and Product.query.count() == 0:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                products_data = json.load(f)
            cat_map = {c.slug: c.id for c in Category.query.all()}
            for item in products_data:
                item_slug = item.get("slug")
                if item_slug:
                    cat_id = cat_map.get(item.get("category_slug"), None)
                    p = Product(
                        category_id=cat_id,
                        name=item.get("name"),
                        slug=item_slug,
                        sku=item.get("sku"),
                        price=item.get("price", 0),
                        stock=item.get("stock", 999),
                        sort_order=item.get("sort_order", 0),
                        image=item.get("image", ""),
                        images=item.get("images", ""),
                        video_file=item.get("video_file", "") or None,
                        video_url=item.get("video_url", "") or None,
                        cad_file=item.get("cad_file", ""),
                        description=item.get("description", ""),
                        specifications=item.get("specifications", ""),
                        status=item.get("status", "active")
                    )
                    db.session.add(p)
            db.session.commit()
            print(f"[INIT] Auto-restored {len(products_data)} products into empty database.")
        except Exception as e:
            db.session.rollback()
            print(f"[INIT ERROR] Failed to restore products: {e}")

    # Auto-restore Orders if table is empty
    orders_json_path = Path(__file__).resolve().parent / "orders_dump.json"
    if orders_json_path.exists() and Order.query.count() == 0:
        try:
            with open(orders_json_path, "r", encoding="utf-8") as f:
                odata = json.load(f)
            for o in odata.get("orders", []):
                created_dt = datetime.fromisoformat(o["created_at"]) if o.get("created_at") else datetime.utcnow()
                new_o = Order(
                    id=o.get("id"),
                    user_id=o.get("user_id", 1),
                    customer_name=o.get("customer_name"),
                    email=o.get("email"),
                    phone=o.get("phone"),
                    address=o.get("address"),
                    note=o.get("note"),
                    total=o.get("total", 0),
                    status=o.get("status", "pending_payment"),
                    created_at=created_dt
                )
                db.session.add(new_o)
            for it in odata.get("items", []):
                new_it = OrderItem(
                    id=it.get("id"),
                    order_id=it.get("order_id"),
                    product_id=it.get("product_id"),
                    product_name=it.get("product_name"),
                    price=it.get("price"),
                    quantity=it.get("quantity"),
                    subtotal=it.get("subtotal")
                )
                db.session.add(new_it)
            db.session.commit()
            print(f"[INIT] Auto-restored {len(odata.get('orders', []))} orders into database.")
        except Exception as e:
            db.session.rollback()
            print(f"[INIT ERROR] Failed to restore orders: {e}")

    # Auto-restore Contacts if table is empty
    contacts_json_path = Path(__file__).resolve().parent / "contacts_dump.json"
    if contacts_json_path.exists() and Contact.query.count() == 0:
        try:
            with open(contacts_json_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            for c in cdata:
                created_dt = datetime.fromisoformat(c["created_at"]) if c.get("created_at") else datetime.utcnow()
                db.session.add(Contact(
                    id=c.get("id"),
                    name=c.get("name"),
                    email=c.get("email"),
                    phone=c.get("phone"),
                    message=c.get("message"),
                    is_read=c.get("is_read", False),
                    created_at=created_dt
                ))
            db.session.commit()
            print(f"[INIT] Auto-restored {len(cdata)} contacts into database.")
        except Exception as e:
            db.session.rollback()
            print(f"[INIT ERROR] Failed to restore contacts: {e}")

    # Auto-restore Page Visits if table is empty
    visits_json_path = Path(__file__).resolve().parent / "visits_dump.json"
    if visits_json_path.exists() and PageVisit.query.count() == 0:
        try:
            with open(visits_json_path, "r", encoding="utf-8") as f:
                vdata = json.load(f)
            for v in vdata:
                created_dt = datetime.fromisoformat(v["created_at"]) if v.get("created_at") else datetime.utcnow()
                db.session.add(PageVisit(
                    id=v.get("id"),
                    ip_address=v.get("ip_address"),
                    user_agent=v.get("user_agent"),
                    path=v.get("path"),
                    user_id=v.get("user_id"),
                    location=v.get("location", "Việt Nam"),
                    device_type=v.get("device_type", "Máy tính"),
                    created_at=created_dt
                ))
            db.session.commit()
            print(f"[INIT] Auto-restored {len(vdata)} visits into database.")
        except Exception as e:
            db.session.rollback()
            print(f"[INIT ERROR] Failed to restore visits: {e}")

    # 1. Kéo toàn bộ ảnh, video, file CAD từ Supabase Cloud về ổ đĩa cục bộ ngay khi khởi động
    restore_all_cloud_assets_to_local()

    # 2. Auto-sync any local uploads to Supabase Cloud
    sync_local_uploads_to_cloud()

    # 3. Đồng bộ danh sách sản phẩm thực tế vào dump file dự phòng
    sync_products_to_dump()

def sync_products_to_dump():
    """Sync all current DB products to products_dump.json so user price changes and product updates persist across deployments."""
    try:
        products = Product.query.order_by(Product.id.asc()).all()
        if not products:
            return
        cat_map = {c.id: c.slug for c in Category.query.all()}
        dump_list = []
        for p in products:
            dump_list.append({
                "category_slug": cat_map.get(p.category_id, ""),
                "name": p.name,
                "slug": p.slug,
                "sku": p.sku or "",
                "price": p.price or 0,
                "stock": p.stock or 999,
                "sort_order": p.sort_order or 0,
                "image": p.image or "",
                "images": p.images or "",
                "video_file": p.video_file or "",
                "video_url": p.video_url or "",
                "cad_file": p.cad_file or "",
                "description": p.description or "",
                "specifications": p.specifications or "",
                "status": p.status or "active"
            })
        json_path = Path(__file__).resolve().parent / "products_dump.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(dump_list, f, ensure_ascii=False, indent=2)
        print(f"[SYNC] Preserved {len(dump_list)} products into products_dump.json")
    except Exception as e:
        print(f"[SYNC ERROR] Failed to sync products to JSON: {e}")

with app.app_context():
    initialize_database()

def start_self_ping():
    """Background thread to ping public site URL every 4 minutes to keep Render awake."""
    import threading, time, urllib.request
    def ping_loop():
        time.sleep(20)
        while True:
            try:
                base_site = (app.config.get("SITE_URL") or "https://nhiemautotech.io.vn").rstrip("/")
                target_url = f"{base_site}/ping"
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

@app.route("/static/uploads/<folder>/<path:filename>")
def serve_upload(folder, filename):
    folder_safe = secure_filename(folder)
    base_upload_dir = Path(app.config["UPLOAD_FOLDER"]).resolve()
    upload_dir = (base_upload_dir / folder_safe).resolve()
    if not str(upload_dir).startswith(str(base_upload_dir)):
        abort(403)
        
    file_path = (upload_dir / filename).resolve()
    # 1. Nếu file đã có sẵn trên ổ đĩa máy chủ, phục vụ trực tiếp kèm cache trình duyệt
    if file_path.exists() and file_path.is_file():
        resp = send_from_directory(upload_dir, filename)
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return resp
        
    # 2. Nếu file BỊ MẤT trên máy tính (lỡ tay xóa hoặc Render khởi động lại container mới):
    # Tự động kéo file từ Đám mây Supabase về và ghi lại vào ổ đĩa, sau đó phục vụ ngay lập tức!
    try:
        asset = CloudAsset.query.filter_by(filename=filename).first()
        if asset and asset.data:
            upload_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(asset.data)
            print(f"[CLOUD RESTORE] Đã tự động phục hồi file '{filename}' từ Đám mây Supabase về máy chủ.")
            resp = send_from_directory(upload_dir, filename)
            resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return resp
    except Exception as e:
        print(f"[CLOUD RESTORE ERROR] Lỗi khi phục hồi '{filename}': {e}")
        
    # 3. Phục hồi dự phòng nếu ảnh hoàn toàn không có: hiển thị ảnh banner mặc định thay vì icon lỗi vỡ ảnh
    if folder == "images":
        default_banner = upload_dir / "thu_vien_thiet_bi_dien_brands_banner.jpg"
        if default_banner.exists():
            resp = send_from_directory(upload_dir, default_banner.name)
            resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return resp
            
    abort(404)

@app.after_request
def add_browser_caching(response):
    """Tự động thêm Cache-Control cho CSS, JS, Fonts, Ảnh để trình duyệt tải trang thứ 2 gần như 0ms."""
    if request.path.startswith("/static/"):
        if "max-age" not in response.headers.get("Cache-Control", ""):
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return response

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
            
            # Check if file exists on disk to attach (auto-restore from Cloud if missing)
            file_on_disk = cad_dir / p.cad_file
            if not file_on_disk.exists():
                try:
                    asset = CloudAsset.query.filter_by(filename=p.cad_file).first()
                    if asset and asset.data:
                        cad_dir.mkdir(parents=True, exist_ok=True)
                        file_on_disk.write_bytes(asset.data)
                except Exception as ce:
                    print(f"[MAIL ATTACH RESTORE ERROR] {ce}")
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
            <p style="margin:0 0 4px;">🌐 Facebook: <a href="https://www.facebook.com/profile.php?id=61593437326233" style="color:#0284c7;">Nhiệm Autotech Electrical</a></p>
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

def send_admin_new_order_email(order):
    """Send real-time alert email to Admin Gmail when a customer places a new order."""
    admin_email = app.config.get("ADMIN_EMAIL") or app.config.get("MAIL_USERNAME") or "nhiem.autotech@gmail.com"
    smtp_user = app.config.get("MAIL_USERNAME")
    smtp_pass = app.config.get("MAIL_PASSWORD")
    smtp_server = app.config.get("MAIL_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_PORT", 587)
    smtp_tls = app.config.get("MAIL_USE_TLS", True)
    sender = app.config.get("MAIL_DEFAULT_SENDER") or (smtp_user if smtp_user else "nhiem.autotech@gmail.com")

    if not smtp_user or not smtp_pass:
        print(f"[ADMIN MAIL WARN] SMTP not configured. New order notification ready for order #{order.id}")
        return False

    try:
        items_str = "<br>".join([f"• <strong>{item.product_name}</strong> (x{item.quantity}): {item.subtotal:,.0f} đ" for item in order.items])
        admin_order_url = public_url("admin_orders")

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family:Arial,sans-serif;line-height:1.6;color:#1e293b;background-color:#f8fafc;padding:20px;">
          <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:24px;border:1px solid #e2e8f0;">
            <div style="background:#0f172a;padding:16px;text-align:center;border-radius:8px;color:#fff;">
              <h2 style="margin:0;color:#ff7a00;">🔥 THÔNG BÁO CÓ ĐƠN HÀNG MỚI #{order.id}</h2>
            </div>
            <p style="font-size:15px;margin-top:16px;">Kính gửi Admin Nhiệm Autotech,</p>
            <p style="font-size:14px;">Khách hàng <strong>{order.customer_name}</strong> vừa đặt một đơn hàng mới trên website:</p>
            
            <div style="background:#f1f5f9;padding:14px;border-radius:6px;margin:16px 0;border-left:4px solid #ff7a00;">
              <div>• <strong>Mã đơn hàng:</strong> #{order.id} (Nội dung CK: <strong>NA{order.id}</strong>)</div>
              <div>• <strong>Khách hàng:</strong> {order.customer_name}</div>
              <div>• <strong>Số điện thoại:</strong> {order.phone}</div>
              <div>• <strong>Email khách:</strong> {order.email or 'Không có'}</div>
              <div>• <strong>Tổng tiền:</strong> <strong style="color:#ff7a00;">{order.total:,.0f} đ</strong></div>
            </div>

            <h4>Chi tiết sản phẩm khách chọn mua:</h4>
            <div style="font-size:14px;background:#fafafa;padding:12px;border-radius:6px;">{items_str}</div>

            <div style="text-align:center;margin-top:24px;">
              <a href="{admin_order_url}" style="background:#ff7a00;color:#fff;padding:12px 24px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;">👉 VÀO TRANG ADMIN DUYỆT ĐƠN NGAY</a>
            </div>
          </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Nhiệm Autotech Admin] 🔥 ĐƠN HÀNG MỚI #{order.id} - {order.customer_name} ({order.total:,.0f}đ)"
        msg["From"] = sender
        msg["To"] = admin_email
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if smtp_tls:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=12)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=12)
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, [admin_email], msg.as_string())
        server.quit()
        print(f"[ADMIN MAIL SUCCESS] Notified admin for new order #{order.id}")
        return True
    except Exception as e:
        print(f"[ADMIN MAIL ERROR] Could not notify admin for order #{order.id}: {e}")
        return False

def send_admin_contact_alert_email(name, phone, email, message, source_tag="Trò chuyện trực tiếp", current_page=None):
    """Send alert email to Admin Gmail when a customer leaves a message, chat, or quote request."""
    admin_email = app.config.get("ADMIN_EMAIL") or app.config.get("MAIL_USERNAME")
    smtp_user = app.config.get("MAIL_USERNAME")
    smtp_pass = app.config.get("MAIL_PASSWORD")
    smtp_server = app.config.get("MAIL_SERVER", "smtp.gmail.com")
    smtp_port = app.config.get("MAIL_PORT", 587)
    smtp_tls = app.config.get("MAIL_USE_TLS", True)
    sender = app.config.get("MAIL_DEFAULT_SENDER") or (smtp_user if smtp_user else "nhiem.autotech@gmail.com")

    if not smtp_user or not smtp_pass or not admin_email:
        try:
            print(f"[ADMIN MAIL INFO] SMTP not configured. Message ready from: {name}")
        except Exception:
            pass
        return False

    try:
        page_html = f"<div>• <strong>Trang khách gửi:</strong> {current_page}</div>" if current_page else ""
        contacts_url = public_url("admin_contacts")
        clean_phone = (phone or "").replace(" ", "").replace("-", "")
        call_link = f'<a href="tel:{clean_phone}" style="background:#10b981;color:#fff;padding:9px 18px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;margin-right:8px;">📞 Gọi cho khách: {phone}</a>' if clean_phone and clean_phone != "Chưacungcấp" else ""
        zalo_link = f'<a href="https://zalo.me/{clean_phone}" style="background:#0088ff;color:#fff;padding:9px 18px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;">💬 Chat Zalo</a>' if clean_phone and clean_phone != "Chưacungcấp" else ""

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family:Arial,sans-serif;line-height:1.6;color:#1e293b;background-color:#f8fafc;padding:20px;">
          <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:24px;border:1px solid #e2e8f0;">
            <div style="background:#0f172a;padding:16px;text-align:center;border-radius:8px;color:#fff;">
              <h2 style="margin:0;color:#ff7a00;">💬 KHÁCH HÀNG LIÊN HỆ: {source_tag.upper()}</h2>
            </div>
            <p style="font-size:15px;margin-top:16px;">Kính gửi Admin Nhiệm Autotech,</p>
            <p style="font-size:14px;">Bạn vừa nhận được thông tin từ khách hàng trên website:</p>
            
            <div style="background:#f1f5f9;padding:14px;border-radius:6px;margin:16px 0;border-left:4px solid #ff7a00;">
              <div>• <strong>Hình thức:</strong> <span style="background:#ff7a00;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{source_tag}</span></div>
              <div>• <strong>Họ tên:</strong> {name}</div>
              <div>• <strong>Số điện thoại:</strong> <strong style="color:#0284c7;font-size:16px;">{phone or 'Khách chưa để lại SĐT'}</strong></div>
              <div>• <strong>Email:</strong> {email or 'Không có'}</div>
              {page_html}
            </div>

            <h4>Nội dung tin nhắn khách gửi:</h4>
            <div style="font-size:14px;background:#fafafa;padding:14px;border-radius:6px;border:1px solid #e2e8f0;white-space:pre-line;">{message}</div>

            <div style="margin-top:20px;">
              {call_link}
              {zalo_link}
            </div>

            <div style="text-align:center;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px;">
              <a href="{contacts_url}" style="background:#0f172a;color:#fff;padding:10px 20px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;font-size:13px;">👉 Xem tất cả tin nhắn trong Quản Trị</a>
            </div>
          </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Nhiệm Autotech] 💬 Khách liên hệ mới: {phone or name} ({source_tag})"
        msg["From"] = sender
        msg["To"] = admin_email
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if smtp_tls:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=12)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=12)
        server.login(smtp_user, smtp_pass)
        server.sendmail(sender, [admin_email], msg.as_string())
        server.quit()
        print(f"[ADMIN MAIL SUCCESS] Notified admin for contact message from {phone or name}")
        return True
    except Exception as e:
        print(f"[ADMIN MAIL ERROR] Could not notify admin for contact message: {e}")
        return False

def add_notification(notif_type, title, message, link=None):
    """Add notification to Admin Notification Center."""
    try:
        n = Notification(
            type=notif_type,
            title=title,
            message=message,
            link=link
        )
        db.session.add(n)
        db.session.commit()
    except Exception as e:
        print(f"[NOTIF ERROR] Could not add notification: {e}")
        db.session.rollback()

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

def _async_resolve_ip_location(ip):
    """Tra cứu vị trí IP trong luồng nền (background thread), không làm nghẽn trang web của khách."""
    if not ip or ip in _IP_LOCATION_CACHE:
        return
    try:
        import urllib.request
        req = urllib.request.Request(f"http://ip-api.com/json/{ip}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=1.5) as res:
            data = json.loads(res.read().decode("utf-8"))
            if data.get("status") == "success":
                city = data.get("city", "")
                region = data.get("regionName", "")
                country = data.get("country", "")
                loc_parts = []
                if city: loc_parts.append(city)
                if region and region != city: loc_parts.append(region)
                if country: loc_parts.append("Việt Nam" if country == "Vietnam" else country)
                if loc_parts:
                    _IP_LOCATION_CACHE[ip] = "📍 " + ", ".join(loc_parts)
    except Exception:
        pass

def get_location_name(ip):
    if not ip or ip in ["127.0.0.1", "::1", "localhost"] or ip.startswith("192.168.") or ip.startswith("10."):
        return "🏠 Nội bộ (Localhost)"
    
    if ip in _IP_LOCATION_CACHE:
        return _IP_LOCATION_CACHE[ip]

    # Non-blocking: Trả về kết quả mặc định ngay lập tức trong 0.0001s, chạy luồng ngầm để tra cứu
    import threading
    threading.Thread(target=_async_resolve_ip_location, args=(ip,), daemon=True).start()
    return "🇻🇳 Việt Nam"

# ==============================================================================
# SIÊU TỐI ƯU HIỆU NĂNG: BỘ NHỚ ĐỆM RAM & XỬ LÝ NỀN KHÔNG NGHẼN (SUB-SECOND SPEED)
# ==============================================================================
_ADMIN_IPS_CACHE = set()
_ADMIN_IPS_CACHE_TIME = 0

def get_admin_ips_cached():
    global _ADMIN_IPS_CACHE, _ADMIN_IPS_CACHE_TIME
    import time
    now = time.time()
    if now - _ADMIN_IPS_CACHE_TIME > 600 or not _ADMIN_IPS_CACHE:
        try:
            _ADMIN_IPS_CACHE = set(r[0] for r in db.session.query(AdminIP.ip_address).all())
            _ADMIN_IPS_CACHE_TIME = now
        except Exception:
            pass
    return _ADMIN_IPS_CACHE

def _async_record_visit(app_obj, visit_dict):
    """Ghi nhận lượt truy cập của khách vào database trong luồng nền (background thread),
    giúp người dùng tải trang lập tức 0ms mà không bị nghẽn chờ ghi database."""
    with app_obj.app_context():
        try:
            v = PageVisit(**visit_dict)
            db.session.add(v)
            db.session.commit()
        except Exception:
            db.session.rollback()

_CATALOG_CACHE = {}

def get_cached_catalog(key, ttl_seconds, fetch_fn):
    """In-memory cache cho danh mục và sản phẩm giúp phản hồi trang web trong 1-5ms
    thay vì phải gửi 5-10 câu truy vấn mạng tới Supabase PostgreSQL ở Singapore."""
    import time
    now = time.time()
    if key in _CATALOG_CACHE:
        cached_val, exp = _CATALOG_CACHE[key]
        if now < exp:
            return cached_val
    val = fetch_fn()
    _CATALOG_CACHE[key] = (val, now + ttl_seconds)
    return val

def clear_catalog_cache():
    """Xóa bộ nhớ đệm ngay khi Admin thêm/sửa/xóa sản phẩm để khách thấy thay đổi ngay lập tức."""
    _CATALOG_CACHE.clear()

@app.context_processor
def inject_admin_badges():
    if session.get("is_admin"):
        try:
            unread_n = Notification.query.filter_by(is_read=False).count()
            unread_c = ChatMessage.query.filter_by(sender="user", is_read_by_admin=False).count()
            return {"unread_notifs": unread_n, "unread_chats": unread_c}
        except Exception:
            return {"unread_notifs": 0, "unread_chats": 0}
    return {"unread_notifs": 0, "unread_chats": 0}

@app.before_request
def redirect_to_custom_domain():
    # Keep health check /ping directly accessible
    if request.path == "/ping":
        return None
    # Tự động chuyển hướng 301 vĩnh viễn sang tên miền chính thức khi truy cập qua bất kỳ domain onrender.com nào
    raw_host = (
        request.headers.get("X-Forwarded-Host")
        or request.headers.get("Host")
        or request.host
        or ""
    )
    host = raw_host.lower().split(":")[0]
    if "onrender.com" in host or "autotech-electrical" in host:
        query = f"?{request.query_string.decode('utf-8')}" if request.query_string else ""
        return redirect(f"https://nhiemautotech.io.vn{request.path}{query}", code=301)
    return None


@app.before_request
def track_visitor():
    req_path_lower = (request.path or "").lower()
    if (
        request.path.startswith("/static")
        or request.path.startswith("/admin")
        or request.path.startswith("/api")
        or request.path in ["/robots.txt", "/sitemap.xml", "/ping", "/favicon.ico"]
        or request.path.endswith(".ico")
        or request.path.endswith(".png")
        or request.path.endswith(".jpg")
        or any(p in req_path_lower for p in SCANNER_PATH_PATTERNS)
    ):
        return
    # 1. Bỏ qua nếu là Admin đang có phiên đăng nhập
    if session.get("is_admin"):
        return
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip:
            ip = ip.split(",")[0].strip()

        # 2. Bỏ qua nếu là IP máy chủ nội bộ hoặc localhost
        if not ip or ip in ["127.0.0.1", "::1", "localhost", "0.0.0.0"]:
            return

        # 3. Bỏ qua nếu IP này đã từng là của Quản trị viên (kiểm tra siêu tốc trong RAM 0.0001s)
        if ip in get_admin_ips_cached():
            return

        # 4. Bỏ qua các dải IP Crawler tự động của Facebook, Google Cloud, và mạng quét cổng nước ngoài
        if any(ip.startswith(prefix) for prefix in BOT_IP_PREFIXES):
            return

        # 5. Bỏ qua bot, crawler, scraper hoặc health check của máy chủ
        user_agent_str = request.headers.get("User-Agent") or (request.user_agent.string if request.user_agent else "") or ""
        ua_lower = user_agent_str.lower()
        if any(bot in ua_lower for bot in [
            "go-http-client", "python-requests", "github-actions", "uptimerobot", "render",
            "curl", "wget", "headlesschrome", "bot", "crawler", "spider", "scraper",
            "facebookexternalhit", "facebot", "googlebot", "bingbot", "yandex",
            "baiduspider", "semrush", "ahrefs", "bytespider", "tiktokbot", "censys", "shodan"
        ]):
            return

        # Ghi nhận nguồn truy cập (HTTP Referer + URL Params fbclid/ttclid/zarsrc + In-App Browser UA)
        incoming_ref = (request.referrer or request.headers.get("Referer") or "").strip()
        full_query = request.query_string.decode("utf-8", errors="ignore").lower()

        detected_ref = ""
        if "fbclid" in full_query or any(fb_tag in ua_lower for fb_tag in ["fban", "fbav", "fb_iab", "fb4a", "fbios"]):
            detected_ref = "https://facebook.com/ (App Facebook)"
        elif "ttclid" in full_query or any(tt_tag in ua_lower for tt_tag in ["tiktok", "musical_ly", "trill", "bytedance"]):
            detected_ref = "https://tiktok.com/ (App TikTok)"
        elif "zarsrc" in full_query or any(zalo_tag in ua_lower for zalo_tag in ["zalo", "zalopc", "zalotheme"]):
            detected_ref = "https://zalo.me/ (App Zalo)"
        elif "gclid" in full_query:
            detected_ref = "https://google.com/ (Google Ads)"
        elif request.args.get("utm_source"):
            u_src = request.args.get("utm_source", "").strip().lower()
            if "fb" in u_src or "facebook" in u_src:
                detected_ref = "https://facebook.com/ (Facebook Campaign)"
            elif "tiktok" in u_src:
                detected_ref = "https://tiktok.com/ (TikTok Campaign)"
            elif "zalo" in u_src:
                detected_ref = "https://zalo.me/ (Zalo Campaign)"
            elif "google" in u_src:
                detected_ref = "https://google.com/ (Google Campaign)"
            elif "youtube" in u_src:
                detected_ref = "https://youtube.com/ (YouTube Campaign)"
            else:
                detected_ref = f"utm:{u_src}"

        # Xác định referrer có hiệu lực
        is_internal = any(domain in incoming_ref.lower() for domain in ["nhiemautotech", "autotech-electrical", "127.0.0.1", "localhost"])
        saved_entry_ref = session.get("entry_referrer")

        if detected_ref:
            effective_ref = detected_ref
            session["entry_referrer"] = detected_ref
        elif incoming_ref and not is_internal:
            effective_ref = incoming_ref
            session["entry_referrer"] = incoming_ref
        else:
            effective_ref = saved_entry_ref or incoming_ref

        # Ghi nhận vào DB bằng luồng nền hoàn toàn KHÔNG NGHẼN (trả trang cho khách ngay lập tức 0ms)
        visit_dict = {
            "user_id": session.get("user_id"),
            "ip_address": ip[:50] if ip else "127.0.0.1",
            "location": get_location_name(ip),
            "device_type": get_device_type(user_agent_str),
            "user_agent": user_agent_str[:250],
            "path": request.path[:250],
            "duration_seconds": 0,
            "referrer": effective_ref[:350] if effective_ref else ""
        }
        import threading
        from flask import current_app
        threading.Thread(target=_async_record_visit, args=(current_app._get_current_object(), visit_dict), daemon=True).start()
    except Exception:
        pass

@app.post("/api/track-duration")
def api_track_duration():
    if session.get("is_admin"):
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    seconds = int(data.get("seconds", 0))
    if seconds <= 0 or seconds > 7200:
        return jsonify({"ok": True})

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    if not ip:
        return jsonify({"ok": True})

    try:
        from datetime import datetime, timedelta
        recent_cutoff = datetime.utcnow() - timedelta(hours=1)
        latest_visit = PageVisit.query.filter(
            PageVisit.ip_address == ip,
            PageVisit.created_at >= recent_cutoff,
            PageVisit.path.notlike('/api/%')
        ).order_by(PageVisit.id.desc()).first()
        if latest_visit:
            latest_visit.duration_seconds = max(latest_visit.duration_seconds or 0, seconds)
            db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({"ok": True})

@app.template_filter('vn_time')
def vn_time_filter(dt, format_str='%d/%m/%Y %H:%M'):
    if not dt:
        return ""
    from datetime import timedelta
    # Database lưu mốc UTC, chuyển đổi sang Múi giờ Việt Nam (GMT+7)
    vn_dt = dt + timedelta(hours=7)
    return vn_dt.strftime(format_str)

@app.template_filter('time_ago')
def time_ago_filter(dt):
    if not dt:
        return ""
    from datetime import datetime, timedelta
    vn_now = datetime.utcnow() + timedelta(hours=7)
    utc_now = datetime.utcnow()
    # Tính chênh lệch dù dt được truyền vào ở mốc UTC hay đã cộng 7h
    diff_vn = abs((vn_now - dt).total_seconds())
    diff_utc = abs((utc_now - dt).total_seconds())
    diff_sec = min(diff_vn, diff_utc)

    if diff_sec < 45:
        return "Vừa xong"
    elif diff_sec < 3600:
        mins = max(1, int(diff_sec // 60))
        return f"{mins} phút trước"
    elif diff_sec < 86400:
        hours = int(diff_sec // 3600)
        return f"{hours} giờ trước"
    elif diff_sec < 172800:
        return "Hôm qua"
    else:
        days = int(diff_sec // 86400)
        return f"{days} ngày trước"

@app.after_request
def prevent_private_pages_from_being_indexed(response):
    private_endpoints = {
        "login", "register", "logout", "cart", "cart_add", "cart_update", "cart_remove",
        "checkout", "payment", "payment_notify_paid", "account_orders", "cad_download", "admin_dashboard", "admin_products",
        "admin_product_add", "admin_product_delete", "admin_orders", "admin_order_status",
        "admin_contacts", "admin_chat",
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
    unread_notifs = 0
    recent_notifs = []
    # Chỉ truy vấn thông báo khi là Admin đang đăng nhập (tiết kiệm 2 câu SQL cho khách vãng lai)
    if session.get("is_admin"):
        try:
            unread_notifs = Notification.query.filter_by(is_read=False).count()
            recent_notifs = Notification.query.order_by(Notification.created_at.desc()).limit(6).all()
        except Exception:
            pass

    return {
        "cart_count": sum(x[1] for x in items),
        "cart_total": total,
        "unread_notifs": unread_notifs,
        "recent_notifs": recent_notifs,
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
    def load_home_data():
        from sqlalchemy.orm import joinedload
        categories = Category.query.order_by(Category.id.asc()).all()
        cat_autocad = Category.query.filter_by(slug="thu-vien-autocad").first()
        cat_panel = Category.query.filter_by(slug="thiet-ke-tu-dien").first()
        cat_automation = Category.query.filter_by(slug="do-an-tu-dong-hoa").first()
        
        cad_products = Product.query.options(joinedload(Product.category)).filter_by(category_id=cat_autocad.id, status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all() if cat_autocad else []
        panel_products = Product.query.options(joinedload(Product.category)).filter_by(category_id=cat_panel.id, status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all() if cat_panel else []
        automation_products = Product.query.options(joinedload(Product.category)).filter_by(category_id=cat_automation.id, status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all() if cat_automation else []
        return {
            "categories": categories,
            "cat_autocad": cat_autocad,
            "cat_panel": cat_panel,
            "cat_automation": cat_automation,
            "cad_products": cad_products,
            "panel_products": panel_products,
            "automation_products": automation_products
        }

    data = get_cached_catalog("home_page_data", 300, load_home_data)
    return render_template("index.html", **data)

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

    if not q and not category:
        from sqlalchemy.orm import joinedload
        prods, cats = get_cached_catalog("all_active_products", 300, lambda: (
            Product.query.options(joinedload(Product.category)).filter_by(status="active").order_by(Product.sort_order.asc(), Product.id.asc()).all(),
            Category.query.all()
        ))
        return render_template("products.html", products=prods, categories=cats, q=q,
                               selected_category=category, canonical_url=public_url("products"))

    from sqlalchemy.orm import joinedload
    query = Product.query.options(joinedload(Product.category)).filter_by(status="active")
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
    categories = get_cached_catalog("all_categories", 300, lambda: Category.query.all())
    return render_template("products.html", products=products, categories=categories, q=q,
                           selected_category=category, canonical_url=public_url("products"))

@app.route("/product/<slug>")
def product_detail(slug):
    if session.get("is_admin"):
        product = Product.query.filter_by(slug=slug).first_or_404()
    else:
        from sqlalchemy.orm import joinedload
        product = get_cached_catalog(f"product_detail_{slug}", 300, lambda: (
            Product.query.options(joinedload(Product.category)).filter_by(slug=slug, status="active").first()
        ))
        if not product:
            abort(404)
    description = (product.description or f"Thông tin, giá và báo giá {product.name} tại Nhiệm Autotech.").strip()
    return render_template("product_detail.html", product=product, seo_description=description[:160],
                           canonical_url=public_url("product_detail", slug=product.slug))

@app.route("/cad-library")
def cad_library():
    from sqlalchemy.orm import joinedload
    products = get_cached_catalog("cad_library_products", 300, lambda: (
        Product.query.options(joinedload(Product.category)).filter(Product.cad_file.isnot(None), Product.status=="active").order_by(Product.created_at.desc()).all()
    ))
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

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(app.root_path, "static", "images"), "logo.jpg", mimetype="image/jpeg")

@app.route("/google79bc38910d6f203a.html")
def google_verify_file():
    return Response("google-site-verification: google79bc38910d6f203a.html", mimetype="text/html")

@app.route("/robots.txt")
def robots_txt():
    base_url = (app.config.get("SITE_URL") or "https://nhiemautotech.io.vn").rstrip("/")
    content = f"User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /checkout\nDisallow: /payment/\nDisallow: /cart\n\nSitemap: {base_url}/sitemap.xml\n"
    return Response(content, mimetype="text/plain; charset=utf-8")

@app.route("/sitemap.xml")
def sitemap_xml():
    try:
        base_url = (app.config.get("SITE_URL") or "https://nhiemautotech.io.vn").rstrip("/")
        static_pages = [
            ("", "daily", "1.0"),
            ("/products", "daily", "0.9"),
            ("/products?category=thu-vien-autocad", "daily", "0.9"),
            ("/products?category=thiet-ke-tu-dien", "daily", "0.8"),
            ("/products?category=do-an-tu-dong-hoa", "daily", "0.8"),
            ("/tra-cuu-don-hang", "weekly", "0.7"),
            ("/quote", "weekly", "0.7"),
            ("/contact", "weekly", "0.7"),
        ]
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        for path, freq, prio in static_pages:
            xml += "  <url>\n"
            xml += f"    <loc>{escape(base_url + path)}</loc>\n"
            xml += f"    <changefreq>{freq}</changefreq>\n"
            xml += f"    <priority>{prio}</priority>\n"
            xml += "  </url>\n"
        
        products = Product.query.filter_by(status="active").all()
        for p in products:
            if p.slug:
                xml += "  <url>\n"
                xml += f"    <loc>{escape(base_url + '/product/' + url_quote(str(p.slug)))}</loc>\n"
                xml += "    <changefreq>weekly</changefreq>\n"
                xml += "    <priority>0.8</priority>\n"
                xml += "  </url>\n"
        xml += "</urlset>\n"
        response = Response(xml, mimetype="application/xml")
        response.headers["Content-Type"] = "application/xml; charset=utf-8"
        return response
    except Exception as e:
        fallback_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  <url><loc>https://nhiemautotech.io.vn/</loc><priority>1.0</priority></url>\n</urlset>'
        response = Response(fallback_xml, mimetype="application/xml")
        response.headers["Content-Type"] = "application/xml; charset=utf-8"
        return response

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
            try:
                asset = CloudAsset.query.filter_by(filename=product.cad_file).first()
                if asset and asset.data:
                    path.mkdir(parents=True, exist_ok=True)
                    file_path.write_bytes(asset.data)
                    print(f"[CAD RESTORE] Đã tự động phục hồi '{product.cad_file}' từ Đám mây Supabase.")
            except Exception as ce:
                print(f"[CAD RESTORE ERROR] {ce}")
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
        try:
            asset = CloudAsset.query.filter_by(filename=product.cad_file).first()
            if asset and asset.data:
                path.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(asset.data)
                print(f"[CAD RESTORE] Đã tự động phục hồi '{product.cad_file}' từ Đám mây Supabase.")
        except Exception as ce:
            print(f"[CAD RESTORE ERROR] {ce}")
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
    add_notification("cart_add", "🛒 Khách thêm sản phẩm vào giỏ hàng", f"Khách vừa thêm '{product.name}' vào giỏ hàng.", link=url_for("admin_dashboard"))
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

        # Trigger Notification & Admin Email Alert
        add_notification("new_order", f"🔥 ĐƠN HÀNG MỚI #{order.id}", f"Khách {customer_name} ({phone}) vừa đặt đơn #{order.id} trị giá {total:,.0f} đ.", link=url_for("admin_orders"))
        send_admin_new_order_email(order)

        # Tự động ghi nhận lượt truy cập của khách vào PageVisit kèm User ID và cập nhật các lượt xem trước đó
        try:
            client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            if client_ip and "," in client_ip:
                client_ip = client_ip.split(",")[0].strip()
            if client_ip:
                ua_str = request.headers.get("User-Agent") or ""
                # Cập nhật toàn bộ các lượt xem trước đó của IP này thành user_id của khách
                PageVisit.query.filter(
                    PageVisit.ip_address == client_ip,
                    PageVisit.user_id.is_(None)
                ).update({"user_id": user.id}, synchronize_session=False)

                db.session.add(PageVisit(
                    user_id=user.id,
                    ip_address=client_ip[:50],
                    location=get_location_name(client_ip),
                    device_type=get_device_type(ua_str),
                    user_agent=ua_str[:250],
                    path=f"/payment/{order.id}",
                    duration_seconds=30,
                    referrer=f"Đặt hàng #{order.id}"
                ))
                db.session.commit()
        except Exception:
            db.session.rollback()

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
        add_notification("new_order", f"💳 Khách báo đã chuyển khoản #{order.id}", f"Khách {order.customer_name} ({order.phone}) vừa bấm BÁO ĐÃ CHUYỂN KHOẢN cho đơn #{order.id} ({order.total:,.0f} đ).", link=url_for("admin_orders"))
        customer_email = order.email or (order.user.email if order.user else "")
        flash(f"Đã ghi nhận thông báo chuyển khoản đơn #{order.id}. Hệ thống đang chờ Admin xác nhận. Sau khi duyệt, file CAD sẽ được gửi tự động về Gmail: {customer_email}", "success")
    elif order.status == "awaiting_confirmation":
        flash(f"Đơn hàng #{order.id} đang trong trạng thái chờ Admin xác nhận.", "info")
    elif order.status == "paid":
        flash(f"Đơn hàng #{order.id} đã được xác nhận thanh toán thành công!", "success")
    return redirect(url_for("payment", order_id=order.id))

@app.route("/tra-cuu-don-hang", methods=["GET", "POST"])
@app.route("/order-lookup", methods=["GET", "POST"])
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
        c = Contact(name=request.form["name"], email=request.form["email"],
                    phone=request.form.get("phone",""), message=request.form["message"])
        db.session.add(c)
        db.session.commit()
        add_notification("contact", f"💬 Tin nhắn liên hệ mới từ {c.name}", f"SĐT: {c.phone} | Content: {c.message[:100]}", link=url_for("admin_contacts"))
        send_admin_contact_alert_email(c.name, c.phone, c.email, c.message, source_tag="Form liên hệ", current_page="/contact")
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
        c = Contact(
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            message=message,
        )
        db.session.add(c)
        db.session.commit()
        add_notification("contact", f"📋 Yêu cầu báo giá mới từ {c.name}", f"SĐT: {c.phone} | Product: {product_name}", link=url_for("admin_contacts"))
        send_admin_contact_alert_email(c.name, c.phone, c.email, c.message, source_tag="Yêu cầu báo giá", current_page="/quote")
        flash("Yêu cầu báo giá của bạn đã được gửi thành công. Nhiệm sẽ liên hệ trong thời gian sớm nhất.", "success")
        return redirect(url_for("quote"))
    return render_template("quote.html", default_product=default_product)

@app.post("/api/chat-support")
def api_chat_support():
    data = request.get_json(silent=True) or request.form
    message = (data.get("message") or "").strip()
    phone = (data.get("phone") or "").strip()
    name = (data.get("name") or "").strip()
    current_page = (data.get("page") or "").strip()
    session_id = (data.get("session_id") or "").strip()

    if not session_id:
        import uuid
        session_id = f"sess_{uuid.uuid4().hex[:16]}"

    if not message and not phone:
        return jsonify({"success": False, "error": "Vui lòng nhập tin nhắn hoặc số điện thoại."}), 400

    # Auto-extract phone number from message if phone wasn't explicitly filled
    if not phone and message:
        import re
        phones = re.findall(r'(?:0|\+84)[3|5|7|8|9][0-9]{8}', message)
        if phones:
            phone = phones[0]

    contact_name = name or (f"Khách {phone[-4:]}" if phone else "Khách trực tuyến")
    note_parts = [f"[Trò chuyện trực tiếp] {message}"]
    if current_page:
        note_parts.append(f"(Trang xem: {current_page})")
    full_message = "\n".join(note_parts)

    # 1. Lưu vào ChatMessage để phục vụ trò chuyện 2 chiều
    chat_msg = ChatMessage(
        session_id=session_id,
        sender="user",
        sender_name=contact_name,
        message=message,
        phone=phone or None,
        is_read_by_admin=False,
        is_read_by_user=True
    )
    db.session.add(chat_msg)

    # 2. Giữ nguyên lưu Contact để tương thích toàn bộ hệ thống cũ & email
    c = Contact(
        name=contact_name,
        email="chat@live-support.local",
        phone=phone or "Chưa cung cấp",
        message=full_message
    )
    db.session.add(c)
    db.session.commit()

    # Tạo thông báo Admin chuyển hướng thẳng tới màn hình chat
    notif_title = f"💬 Khách nhắn tin trực tiếp: {phone if phone else contact_name}"
    notif_msg = f"{contact_name}: {message[:120]}"
    add_notification("contact", notif_title, notif_msg, link=url_for("admin_chat", session_id=session_id))

    # Gửi email cảnh báo cho Admin (nếu cấu hình SMTP)
    try:
        send_admin_contact_alert_email(contact_name, phone, None, message, source_tag="Trò chuyện trực tiếp", current_page=current_page)
    except Exception as e:
        print(f"[MAIL ALERT WARN] Could not send email alert: {e}")

    # Phản hồi tự động theo số lần khách gửi:
    user_msg_count = ChatMessage.query.filter_by(session_id=session_id, sender="user").count()
    if user_msg_count <= 1:
        reply = "Cảm ơn bạn! Nhiệm Autotech đã nhận được thông tin. Chúng tôi sẽ phản hồi hoặc liên hệ qua SĐT của bạn ngay nhé!"
    else:
        reply = "Cảm ơn bạn! Nhiệm Autotech đã nhận được tin nhắn. Bộ phận kỹ thuật sẽ phản hồi trực tiếp cho bạn tại đây ngay nhé!"

    return jsonify({
        "success": True,
        "reply": reply,
        "phone_recorded": bool(phone),
        "session_id": session_id,
        "message_id": chat_msg.id
    })

@app.get("/api/chat-poll")
def api_chat_poll():
    session_id = request.args.get("session_id", "").strip()
    if not session_id:
        return jsonify({"success": False, "messages": []})

    try:
        after_id = int(request.args.get("after_id", 0))
    except (ValueError, TypeError):
        after_id = 0

    query = ChatMessage.query.filter(
        ChatMessage.session_id == session_id,
        ChatMessage.id > after_id
    ).order_by(ChatMessage.id.asc())

    messages = query.all()
    out = []
    has_admin = False
    for m in messages:
        if m.sender == "admin":
            m.is_read_by_user = True
            has_admin = True
        out.append({
            "id": m.id,
            "sender": m.sender,
            "sender_name": m.sender_name,
            "message": m.message,
            "time": m.created_at.strftime("%H:%M"),
            "date": m.created_at.strftime("%d/%m/%Y")
        })

    if has_admin:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return jsonify({"success": True, "messages": out})


@app.route("/login", methods=["GET","POST"])
@app.route("/admin/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].lower().strip()).first()
        if user and check_password_hash(user.password_hash, request.form["password"]):
            session["user_id"], session["is_admin"] = user.id, user.is_admin
            if user.is_admin:
                admin_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
                if admin_ip and "," in admin_ip:
                    admin_ip = admin_ip.split(",")[0].strip()
                if admin_ip and admin_ip not in ["127.0.0.1", "::1", "localhost", "0.0.0.0"]:
                    try:
                        if not AdminIP.query.filter_by(ip_address=admin_ip).first():
                            db.session.add(AdminIP(ip_address=admin_ip))
                        PageVisit.query.filter_by(ip_address=admin_ip).delete()
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
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
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    cat_autocad = Category.query.filter_by(slug="thu-vien-autocad").first()
    cat_panel = Category.query.filter_by(slug="thiet-ke-tu-dien").first()
    cat_automation = Category.query.filter_by(slug="do-an-tu-dong-hoa").first()

    cad_count = Product.query.filter_by(category_id=cat_autocad.id).count() if cat_autocad else 0
    panel_count = Product.query.filter_by(category_id=cat_panel.id).count() if cat_panel else 0
    automation_count = Product.query.filter_by(category_id=cat_automation.id).count() if cat_automation else 0

    from datetime import datetime, date, time, timedelta
    from collections import defaultdict

    # Chuẩn hóa mốc thời gian theo Giờ Việt Nam (GMT+7)
    vn_now = datetime.utcnow() + timedelta(hours=7)
    vn_today = vn_now.date()

    today_start = datetime.combine(vn_today, time.min) - timedelta(hours=7)
    yesterday_start = datetime.combine(vn_today - timedelta(days=1), time.min) - timedelta(hours=7)
    yesterday_end = datetime.combine(vn_today - timedelta(days=1), time.max) - timedelta(hours=7)
    week_start = datetime.combine(vn_today - timedelta(days=6), time.min) - timedelta(hours=7)
    month_start = datetime.combine(vn_today - timedelta(days=29), time.min) - timedelta(hours=7)

    # 1. Tự động nhận diện và lưu IP của Quản trị viên khi mở trang Admin
    current_admin_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if current_admin_ip and "," in current_admin_ip:
        current_admin_ip = current_admin_ip.split(",")[0].strip()
    if current_admin_ip and current_admin_ip not in ["127.0.0.1", "::1", "localhost", "0.0.0.0"]:
        try:
            if not AdminIP.query.filter_by(ip_address=current_admin_ip).first():
                db.session.add(AdminIP(ip_address=current_admin_ip))
            # Xóa sạch mọi vết truy cập của IP admin trong PageVisit
            PageVisit.query.filter_by(ip_address=current_admin_ip).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()

    # 2. Lấy danh sách toàn bộ IP của Quản trị viên và Máy chủ để loại trừ 100%
    try:
        known_admin_ips = [r.ip_address for r in AdminIP.query.all()]
    except Exception:
        known_admin_ips = []
    excluded_ips = set(["127.0.0.1", "::1", "localhost", "0.0.0.0"] + known_admin_ips)

    bot_prefix_filters = [PageVisit.ip_address.notlike(f"{p}%") for p in BOT_IP_PREFIXES]
    scanner_path_filters = [
        PageVisit.path.notlike(f"%{p}%") for p in ["/wp-", ".php", ".env", "/ipfs/", "xmlrpc", "cgi-bin", "phpmyadmin", "boaform"]
    ]

    # Bộ lọc loại trừ IP admin, IP máy chủ nội bộ, bot/crawler, scanner rác, API nội bộ và favicon
    clean_filter = and_(
        PageVisit.ip_address.notin_(list(excluded_ips)),
        *bot_prefix_filters,
        *scanner_path_filters,
        or_(PageVisit.path == None, and_(
            PageVisit.path.notlike('/api/%'),
            PageVisit.path.notin_(['/favicon.ico', '/null', '/.file', '/robots.txt', '/sitemap.xml', '/ping'])
        ))
    )

    total_visits = PageVisit.query.filter(clean_filter).count()
    today_visits = PageVisit.query.filter(PageVisit.created_at >= today_start, clean_filter).count()
    unique_today = db.session.query(PageVisit.ip_address).filter(PageVisit.created_at >= today_start, clean_filter).distinct().count()

    yesterday_visits = PageVisit.query.filter(PageVisit.created_at >= yesterday_start, PageVisit.created_at <= yesterday_end, clean_filter).count()
    unique_yesterday = db.session.query(PageVisit.ip_address).filter(PageVisit.created_at >= yesterday_start, PageVisit.created_at <= yesterday_end, clean_filter).distinct().count()

    week_visits = PageVisit.query.filter(PageVisit.created_at >= week_start, clean_filter).count()
    unique_week = db.session.query(PageVisit.ip_address).filter(PageVisit.created_at >= week_start, clean_filter).distinct().count()

    month_visits = PageVisit.query.filter(PageVisit.created_at >= month_start, clean_filter).count()
    unique_month = db.session.query(PageVisit.ip_address).filter(PageVisit.created_at >= month_start, clean_filter).distinct().count()

    daily_stats = []
    for i in range(7):
        day_date = vn_today - timedelta(days=i)
        day_start = datetime.combine(day_date, time.min) - timedelta(hours=7)
        day_end = datetime.combine(day_date, time.max) - timedelta(hours=7)
        v_count = PageVisit.query.filter(PageVisit.created_at >= day_start, PageVisit.created_at <= day_end, clean_filter).count()
        u_count = db.session.query(PageVisit.ip_address).filter(PageVisit.created_at >= day_start, PageVisit.created_at <= day_end, clean_filter).distinct().count()

        day_label = "Hôm nay" if i == 0 else ("Hôm qua" if i == 1 else f"Thứ {day_date.isoweekday() + 1}" if day_date.isoweekday() < 7 else "Chủ Nhật")
        daily_stats.append({
            "date_str": day_date.strftime("%d/%m/%Y"),
            "day_label": day_label,
            "visits": v_count,
            "uniques": u_count
        })

    def format_visit_duration(seconds, page_cnt):
        if seconds <= 5 and page_cnt <= 1:
            return "~30 giây - 1 phút"
        if seconds < 60:
            return f"{int(seconds)} giây"
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        if mins < 60:
            return f"{mins} phút {secs}s" if secs > 0 else f"{mins} phút"
        hrs = int(mins // 60)
        rmins = int(mins % 60)
        return f"{hrs} giờ {rmins}p"

    def parse_traffic_source(ref_str, ua_str=""):
        ref_lower = (ref_str or "").lower()
        ua_lower = (ua_str or "").lower()

        # Facebook (Link web, link app, fbclid, hoặc in-app webview)
        if (
            "facebook.com" in ref_lower
            or "fb.me" in ref_lower
            or "fb.com" in ref_lower
            or "l.facebook" in ref_lower
            or "fbclid" in ref_lower
            or any(fb_tag in ua_lower for fb_tag in ["fban", "fbav", "fb_iab", "fb4a", "fbios"])
        ):
            return {
                "source": "facebook",
                "name": "Facebook",
                "icon": "🔵",
                "badge_bg": "#eff6ff",
                "badge_color": "#1d4ed8",
                "badge_border": "#bfdbfe"
            }

        # TikTok (Link web, bio link, ttclid, hoặc in-app webview)
        if (
            "tiktok.com" in ref_lower
            or "ttclid" in ref_lower
            or any(tt_tag in ua_lower for tt_tag in ["tiktok", "musical_ly", "trill", "bytedance"])
        ):
            return {
                "source": "tiktok",
                "name": "TikTok",
                "icon": "⚫",
                "badge_bg": "#0f172a",
                "badge_color": "#ffffff",
                "badge_border": "#0f172a"
            }

        # Zalo (Link chia sẻ, zarsrc, hoặc app Zalo)
        if "zalo" in ref_lower or "zarsrc" in ref_lower or any(zalo_tag in ua_lower for zalo_tag in ["zalo", "zalopc", "zalotheme"]):
            return {
                "source": "zalo",
                "name": "Zalo",
                "icon": "💬",
                "badge_bg": "#eff6ff",
                "badge_color": "#0284c7",
                "badge_border": "#bae6fd"
            }

        # Google Search hoặc Google Ads
        if "google." in ref_lower or "gclid" in ref_lower:
            return {
                "source": "google",
                "name": "Google Tìm kiếm",
                "icon": "🔍",
                "badge_bg": "#f0fdf4",
                "badge_color": "#15803d",
                "badge_border": "#bbf7d0"
            }

        # YouTube
        if "youtube.com" in ref_lower or "youtu.be" in ref_lower:
            return {
                "source": "youtube",
                "name": "YouTube",
                "icon": "🔴",
                "badge_bg": "#fef2f2",
                "badge_color": "#b91c1c",
                "badge_border": "#fecaca"
            }

        # Bing Search
        if "bing.com" in ref_lower:
            return {
                "source": "bing",
                "name": "Bing Search",
                "icon": "🔎",
                "badge_bg": "#f0f9ff",
                "badge_color": "#0369a1",
                "badge_border": "#bae6fd"
            }

        # ChatGPT / Trí tuệ nhân tạo OpenAI
        if "chatgpt" in ref_lower or "openai" in ref_lower:
            return {
                "source": "chatgpt",
                "name": "ChatGPT (OpenAI)",
                "icon": "🤖",
                "badge_bg": "#ecfdf5",
                "badge_color": "#047857",
                "badge_border": "#a7f3d0"
            }

        # Yahoo Tìm kiếm
        if "yahoo.com" in ref_lower:
            return {
                "source": "yahoo",
                "name": "Yahoo Tìm Kiếm",
                "icon": "🟣",
                "badge_bg": "#f5f3ff",
                "badge_color": "#6b21a8",
                "badge_border": "#ddd6fe"
            }

        # Chuyển hướng tên miền cũ Render hoặc tiền tố www
        if any(dom in ref_lower for dom in ["autotech-electrical.onrender.com", "www.nhiemautotech.io.vn", "onrender.com"]):
            return {
                "source": "redirect",
                "name": "Chuyển tiếp (Render / WWW)",
                "icon": "🔄",
                "badge_bg": "#f8fafc",
                "badge_color": "#475569",
                "badge_border": "#cbd5e1"
            }

        if not ref_lower:
            return {
                "source": "direct",
                "name": "Trực tiếp (Gõ URL / Bookmark)",
                "icon": "🌐",
                "badge_bg": "#f1f5f9",
                "badge_color": "#475569",
                "badge_border": "#e2e8f0"
            }

        # External websites
        try:
            from urllib.parse import urlparse
            domain = urlparse(ref_str).netloc
            name = domain if domain else ref_str[:30]
        except Exception:
            name = ref_str[:30]
        return {
            "source": "external",
            "name": name,
            "icon": "🔗",
            "badge_bg": "#f8fafc",
            "badge_color": "#334155",
            "badge_border": "#cbd5e1"
        }

    # Tổng hợp thống kê chi tiết theo từng IP trong ngày (Hôm nay)
    visits_today = PageVisit.query.filter(
        PageVisit.created_at >= today_start,
        clean_filter
    ).order_by(PageVisit.created_at.desc()).all()

    today_ip_map = defaultdict(list)
    for v in visits_today:
        today_ip_map[v.ip_address].append(v)

    now_dt = datetime.utcnow()
    ip_stats_today = []
    for ip, vlist in today_ip_map.items():
        if any(ip.startswith(prefix) for prefix in BOT_IP_PREFIXES):
            continue
        if ip in excluded_ips:
            continue

        seen_paths = []
        for v in vlist:
            clean_p = v.path or "/"
            if clean_p not in seen_paths and clean_p not in ["/favicon.ico", "/null", "/.file", "/robots.txt", "/sitemap.xml", "/ping"]:
                seen_paths.append(clean_p)

        # Bỏ qua nếu IP này không xem bất kỳ trang nội dung thật nào hoặc chỉ quét đường dẫn bot rác
        if not seen_paths or all(any(p in path.lower() for p in SCANNER_PATH_PATTERNS) for path in seen_paths):
            continue

        vlist_sorted = sorted(vlist, key=lambda x: x.created_at)
        first_t = vlist_sorted[0].created_at
        last_t = vlist_sorted[-1].created_at
        dur_sec = (last_t - first_t).total_seconds() + sum(v.duration_seconds or 0 for v in vlist)

        user_name = None
        user_email = None
        user_orders = []
        for v in vlist:
            if v.user:
                user_name = v.user.name
                user_email = v.user.email
                try:
                    user_orders = Order.query.filter_by(user_id=v.user.id).order_by(Order.id.desc()).all()
                except Exception:
                    user_orders = []
                break

        # Tìm nguồn referrer hoặc User-Agent bên ngoài ban đầu của IP này
        ip_referrer = ""
        ip_ua = ""
        for v in vlist_sorted:
            if v.user_agent and not ip_ua:
                ip_ua = v.user_agent
            r = (v.referrer or "").strip()
            if r and not any(internal in r.lower() for internal in ["nhiemautotech", "autotech-electrical", "127.0.0.1", "localhost"]):
                ip_referrer = r
                break
        if not ip_referrer:
            for v in vlist_sorted:
                if v.referrer and v.referrer.strip():
                    ip_referrer = v.referrer.strip()
                    break

        traffic_source = parse_traffic_source(ip_referrer, ip_ua)
        is_online = (now_dt - last_t).total_seconds() <= 180

        # Chuyển đổi mốc thời gian sang Giờ Việt Nam (GMT+7)
        first_t_vn = first_t + timedelta(hours=7)
        last_t_vn = last_t + timedelta(hours=7)

        raw_loc = vlist[0].location or "Việt Nam"
        loc_str = raw_loc.lower()
        is_vn = any(kw in loc_str for kw in [
            "việt nam", "vietnam", "viet nam", "hà nội", "hanoi", "hồ chí minh", "ho chi minh",
            "đà nẵng", "da nang", "hải phòng", "hai phong", "cần thơ", "can tho",
            "bình dương", "đồng nai", "vũng tàu", "bắc ninh", "nam định", "thái bình"
        ]) or ("🇻🇳" in raw_loc)

        ip_stats_today.append({
            "ip_address": ip,
            "location": raw_loc,
            "is_vietnam": is_vn,
            "user_name": user_name,
            "user_email": user_email,
            "orders": user_orders,
            "page_count": len([v for v in vlist if v.path != '/favicon.ico']),
            "paths": seen_paths if seen_paths else ["/"],
            "first_seen": first_t_vn,
            "last_seen": last_t_vn,
            "duration_formatted": format_visit_duration(dur_sec, len(vlist)),
            "duration_sec": dur_sec,
            "traffic_source": traffic_source,
            "raw_referrer": ip_referrer,
            "is_online": is_online
        })

    ip_stats_today.sort(key=lambda x: x["last_seen"], reverse=True)
    vn_visitors_count = len([x for x in ip_stats_today if x.get("is_vietnam")])

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
                           yesterday_visits=yesterday_visits,
                           unique_yesterday=unique_yesterday,
                           week_visits=week_visits,
                           unique_week=unique_week,
                           month_visits=month_visits,
                           unique_month=unique_month,
                           daily_stats=daily_stats,
                           ip_stats_today=ip_stats_today,
                           vn_visitors_count=vn_visitors_count,
                           current_admin_ip=current_admin_ip)

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
        img_bytes = image.read()
        image.seek(0)
        with open(image_path / filename, "wb") as cf:
            cf.write(img_bytes)
        import mimetypes
        m_type, _ = mimetypes.guess_type(filename)
        save_file_to_cloud(filename, img_bytes, asset_type="image", mime_type=m_type or "image/jpeg")
        category.image = filename
        db.session.commit()
        clear_catalog_cache()
        flash("Đã cập nhật ảnh danh mục (đã đồng bộ lên đám mây).", "success")
        return redirect(url_for("admin_categories"))
    return render_template("admin/categories.html", categories=Category.query.order_by(Category.name).all())

def vietnamese_slugify(text):
    if not text:
        return ""
    import unicodedata, re
    text = unicodedata.normalize('NFC', text).lower().strip()
    viet_map = {
        'à': 'a', 'á': 'a', 'ả': 'a', 'ã': 'a', 'ạ': 'a',
        'ă': 'a', 'ằ': 'a', 'ắ': 'a', 'ẳ': 'a', 'ẵ': 'a', 'ặ': 'a',
        'â': 'a', 'ầ': 'a', 'ấ': 'a', 'ẩ': 'a', 'ẫ': 'a', 'ậ': 'a',
        'đ': 'd',
        'è': 'e', 'é': 'e', 'ẻ': 'e', 'ẽ': 'e', 'ẹ': 'e',
        'ê': 'e', 'ề': 'e', 'ế': 'e', 'ể': 'e', 'ễ': 'e', 'ệ': 'e',
        'ì': 'i', 'í': 'i', 'ỉ': 'i', 'ĩ': 'i', 'ị': 'i',
        'ò': 'o', 'ó': 'o', 'ỏ': 'o', 'õ': 'o', 'ọ': 'o',
        'ô': 'o', 'ồ': 'o', 'ố': 'o', 'ổ': 'o', 'ỗ': 'o', 'ộ': 'o',
        'ơ': 'o', 'ờ': 'o', 'ớ': 'o', 'ở': 'o', 'ỡ': 'o', 'ợ': 'o',
        'ù': 'u', 'ú': 'u', 'ủ': 'u', 'ũ': 'u', 'ụ': 'u',
        'ư': 'u', 'ừ': 'u', 'ứ': 'u', 'ử': 'u', 'ữ': 'u', 'ự': 'u',
        'ỳ': 'y', 'ý': 'y', 'ỷ': 'y', 'ỹ': 'y', 'ỵ': 'y',
    }
    for k, v in viet_map.items():
        text = text.replace(k, v)
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text

def save_product_image(fobj, prefix="img"):
    if not fobj or not fobj.filename:
        return None
    orig_name = fobj.filename.strip()
    ext = Path(orig_name).suffix.lower()
    if not ext or ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
        ext = ".jpg"
    import uuid, time, re, mimetypes, io
    clean_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '-', prefix).strip('-') or 'img'
    fname = f"{clean_prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}{ext}"
    img_dir = Path(app.config["UPLOAD_FOLDER"]) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = fobj.read()
    fobj.seek(0)

    # Tự động nén và resize ảnh thông minh bằng Pillow nếu là ảnh chụp từ điện thoại nặng
    final_bytes = raw_bytes
    if ext in [".jpg", ".jpeg", ".png", ".webp"]:
        try:
            from PIL import Image, ImageOps
            im = Image.open(io.BytesIO(raw_bytes))
            im = ImageOps.exif_transpose(im) # Tự động xoay đúng chiều nếu chụp bằng iPhone/Android

            # Thu nhỏ nếu chiều rộng hoặc chiều cao vượt quá 1600px
            max_size = 1600
            if im.width > max_size or im.height > max_size:
                im.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            out_io = io.BytesIO()
            if ext in [".jpg", ".jpeg"]:
                if im.mode in ("RGBA", "P"):
                    im = im.convert("RGB")
                im.save(out_io, format="JPEG", quality=85, optimize=True)
            elif ext == ".png":
                # Nén tối ưu PNG
                im.save(out_io, format="PNG", optimize=True)
            elif ext == ".webp":
                im.save(out_io, format="WEBP", quality=85)

            compressed_data = out_io.getvalue()
            if compressed_data and len(compressed_data) < len(raw_bytes):
                final_bytes = compressed_data
                print(f"[IMAGE COMPRESS] Tự động nén '{fname}' từ {len(raw_bytes)} bytes xuống còn {len(final_bytes)} bytes.")
        except Exception as pe:
            print(f"[IMAGE COMPRESS WARN] Bỏ qua nén cho {fname}: {pe}")
            final_bytes = raw_bytes

    with open(img_dir / fname, "wb") as f:
        f.write(final_bytes)
    m_type, _ = mimetypes.guess_type(fname)
    save_file_to_cloud(fname, final_bytes, asset_type="image", mime_type=m_type or "image/jpeg")
    return fname


@app.route("/admin/products/add", methods=["GET","POST"])
@admin_required
def admin_product_add():
    import unicodedata, re
    categories = Category.query.all()
    # Hỗ trợ chọn sẵn danh mục theo URL ?category=do-an-tu-dong-hoa hoặc ?category_id=7
    req_cat = request.args.get("category", "").strip()
    req_cat_id = request.args.get("category_id", type=int)
    default_category_id = None
    if req_cat:
        matched = Category.query.filter_by(slug=req_cat).first()
        if matched:
            default_category_id = matched.id
    elif req_cat_id:
        matched = db.session.get(Category, req_cat_id)
        if matched:
            default_category_id = matched.id
    else:
        # Kiểm tra referrer nếu người dùng vừa bấm từ trang mục 1/2/3
        ref = request.referrer or ""
        if "category=do-an-tu-dong-hoa" in ref or "do-an-tu-dong-hoa" in ref:
            c3 = Category.query.filter_by(slug="do-an-tu-dong-hoa").first()
            if c3: default_category_id = c3.id
        elif "category=thiet-ke-tu-dien" in ref or "thiet-ke-tu-dien" in ref:
            c2 = Category.query.filter_by(slug="thiet-ke-tu-dien").first()
            if c2: default_category_id = c2.id
        elif "category=thu-vien-autocad" in ref or "thu-vien-autocad" in ref:
            c1 = Category.query.filter_by(slug="thu-vien-autocad").first()
            if c1: default_category_id = c1.id

    if request.method == "POST":
        name = request.form["name"].strip()
        slug = request.form.get("slug", "").strip().lower()
        if not slug:
            slug = vietnamese_slugify(name)
        price = float(request.form.get("price", 0) or 0)
        
        # Nhận diện danh mục được chọn từ form
        cat_id_val = request.form.get("category_id")
        selected_cat_id = int(cat_id_val) if cat_id_val else default_category_id
        
        # BACKEND SMART DETECTION / SANITY CHECK:
        # Tự động bảo vệ tránh đăng nhầm danh mục khi người dùng thêm sản phẩm
        cat_by_slug = {c.slug: c.id for c in categories}
        cat_c3_id = cat_by_slug.get("do-an-tu-dong-hoa")
        cat_c2_id = cat_by_slug.get("thiet-ke-tu-dien")
        cat_c1_id = cat_by_slug.get("thu-vien-autocad")
        
        name_nfc = unicodedata.normalize("NFC", name).lower()
        name_ascii = vietnamese_slugify(name)
        
        # Từ khóa và tiền tố nhận diện Mục 3 (Tự Động Hóa & Đồ Án)
        m3_keys = [
            "mo hinh", "mô hình", "do an", "đồ án", "solidworks", "stm32", "arduino",
            "bang tai", "băng tải", "phan loai", "phân loại", "camera", "trai cay", "trái cây",
            "bai gui xe", "bãi gửi xe", "kho luu kho", "kho lưu kho", "robot", "scada",
            "tia portal", "plc s7", "s7-1200", "s7-1500"
        ]
        is_m3_prefix = any(name_nfc.startswith(p) for p in ["3.", "3 ", "#3", "mục 3", "muc 3"])
        is_m3_keyword = any(k in name_nfc or k in name_ascii for k in m3_keys)
        
        # Từ khóa và tiền tố nhận diện Mục 2 (Thiết Kế Tủ Điện & Mạch Điều Khiển)
        m2_keys = [
            "layout tu", "layout tủ", "tu msb", "tủ msb", "tu ats", "tủ ats",
            "tu bu", "tủ bù", "tu pccc", "tủ pccc", "tu db", "tủ db", "form tu", "form vỏ tủ",
            "mach dieu khien", "mạch điều khiển"
        ]
        is_m2_prefix = any(name_nfc.startswith(p) for p in ["2.", "2 ", "#2", "mục 2", "muc 2"])
        is_m2_keyword = any(k in name_nfc or k in name_ascii for k in m2_keys)
        
        # Nếu sản phẩm rõ ràng thuộc Mục 3 nhưng bị gán nhầm sang Mục 1:
        if (is_m3_prefix or is_m3_keyword) and cat_c3_id:
            if not selected_cat_id or selected_cat_id == cat_c1_id:
                selected_cat_id = cat_c3_id
        # Nếu sản phẩm rõ ràng thuộc Mục 2 nhưng bị gán nhầm sang Mục 1:
        elif (is_m2_prefix or is_m2_keyword) and cat_c2_id:
            if not selected_cat_id or selected_cat_id == cat_c1_id:
                selected_cat_id = cat_c2_id
        
        if not selected_cat_id:
            selected_cat_id = default_category_id or (categories[0].id if categories else 1)
        
        # Tự động suy luận thứ tự sort_order nếu tên bắt đầu bằng số thứ tự (ví dụ: 1., 2., 3.)
        sort_order = int(request.form.get("sort_order", 0) or 0)
        if sort_order == 0:
            match_num = re.match(r'^(?:#)?(\d+)[\.\s\-]', name.strip())
            if match_num:
                sort_order = int(match_num.group(1))
        
        product = Product(name=name, slug=slug, sku=request.form.get("sku",""),
                          description=request.form.get("description",""),
                          specifications=request.form.get("specifications",""),
                          price=price, stock=int(request.form.get("stock",999) or 999),
                          category_id=selected_cat_id,
                          sort_order=sort_order,
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
                import time, uuid, mimetypes
                v_filename = f"{slug}-video-{int(time.time())}.{ext}"
                video_path = Path(app.config["UPLOAD_FOLDER"]) / "videos"
                video_path.mkdir(parents=True, exist_ok=True)
                v_bytes = video.read()
                video.seek(0)
                with open(video_path / v_filename, "wb") as vf:
                    vf.write(v_bytes)
                v_mime, _ = mimetypes.guess_type(v_filename)
                save_file_to_cloud(v_filename, v_bytes, asset_type="video", mime_type=v_mime or f"video/{ext}")
                product.video_file = v_filename

        # 5. CAD file
        cad = request.files.get("cad_file")
        if cad and cad.filename:
            ext = Path(cad.filename).suffix.lower().lstrip(".")
            if ext not in ALLOWED_DOCS:
                flash(f"Định dạng file .{ext} chưa được hỗ trợ. Hãy nén dạng .zip hoặc .rar hoặc .dwg", "danger")
                return render_template("admin/product_add.html", categories=categories)
            import mimetypes
            filename = f"{slug}-{secure_filename(cad.filename)}"
            cad_path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
            cad_path.mkdir(parents=True, exist_ok=True)
            c_bytes = cad.read()
            cad.seek(0)
            with open(cad_path / filename, "wb") as cf:
                cf.write(c_bytes)
            c_mime, _ = mimetypes.guess_type(filename)
            save_file_to_cloud(filename, c_bytes, asset_type="cad", mime_type=c_mime or "application/octet-stream")
            product.cad_file = filename
            
        db.session.add(product)
        db.session.commit()
        clear_catalog_cache()
        sync_products_to_dump()
        flash(f"Đã đăng '{product.name}' thành công lên danh mục!", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_add.html", categories=categories, default_category_id=default_category_id)

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
                import time, mimetypes
                v_filename = f"{product.slug}-video-{int(time.time())}.{ext}"
                video_path = Path(app.config["UPLOAD_FOLDER"]) / "videos"
                video_path.mkdir(parents=True, exist_ok=True)
                v_bytes = video.read()
                video.seek(0)
                with open(video_path / v_filename, "wb") as vf:
                    vf.write(v_bytes)
                v_mime, _ = mimetypes.guess_type(v_filename)
                save_file_to_cloud(v_filename, v_bytes, asset_type="video", mime_type=v_mime or f"video/{ext}")
                product.video_file = v_filename

        # 6. CAD file update
        cad = request.files.get("cad_file")
        if cad and cad.filename:
            ext = Path(cad.filename).suffix.lower().lstrip(".")
            if ext not in ALLOWED_DOCS:
                flash(f"Định dạng file .{ext} chưa được hỗ trợ.", "danger")
                return render_template("admin/product_edit.html", product=product, categories=categories)
            import mimetypes
            filename = f"{product.slug}-{secure_filename(cad.filename)}"
            cad_path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
            cad_path.mkdir(parents=True, exist_ok=True)
            c_bytes = cad.read()
            cad.seek(0)
            with open(cad_path / filename, "wb") as cf:
                cf.write(c_bytes)
            c_mime, _ = mimetypes.guess_type(filename)
            save_file_to_cloud(filename, c_bytes, asset_type="cad", mime_type=c_mime or "application/octet-stream")
            product.cad_file = filename
            
        db.session.commit()
        clear_catalog_cache()
        sync_products_to_dump()
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
    clear_catalog_cache()
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
    clear_catalog_cache()
    flash(f"Đã xóa ảnh khỏi sản phẩm thành công.", "info")
    return redirect(url_for("admin_product_edit", product_id=product.id))

@app.post("/admin/products/toggle/<int:product_id>")
@admin_required
def admin_product_toggle(product_id):
    p = db.session.get(Product, product_id)
    if p:
        p.status = "hidden" if p.status == "active" else "active"
        db.session.commit()
        clear_catalog_cache()
        sync_products_to_dump()
        flash(f"Đã chuyển trạng thái sản phẩm #{p.id} thành '{p.status}'.", "info")
    return redirect(url_for("admin_products"))

@app.post("/admin/products/delete/<int:product_id>")
@admin_required
def admin_product_delete(product_id):
    p = db.session.get(Product, product_id)
    if p:
        db.session.delete(p)
        db.session.commit()
        clear_catalog_cache()
        sync_products_to_dump()
        flash("Đã xóa sản phẩm khỏi hệ thống.", "info")
    return redirect(url_for("admin_products"))

@app.route("/admin/products/<int:product_id>/quick-category/<int:category_id>", methods=["POST", "GET"])
@admin_required
def admin_product_quick_category(product_id, category_id):
    product = db.session.get(Product, product_id)
    cat = db.session.get(Category, category_id)
    if product and cat:
        product.category_id = cat.id
        db.session.commit()
        clear_catalog_cache()
        sync_products_to_dump()
        flash(f"Đã chuyển '{product.name}' sang '{cat.name}' thành công!", "success")
    return redirect(request.referrer or url_for("admin_products"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    status_filter = request.args.get("status", "").strip()
    query = Order.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    orders = query.order_by(Order.created_at.desc()).all()
    
    counts = {
        "all": Order.query.count(),
        "awaiting": Order.query.filter_by(status="awaiting_confirmation").count(),
        "pending": Order.query.filter_by(status="pending_payment").count(),
        "paid": Order.query.filter_by(status="paid").count(),
        "cancelled": Order.query.filter_by(status="cancelled").count(),
    }
    return render_template("admin/orders.html", orders=orders, counts=counts, selected_status=status_filter)

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

@app.post("/admin/orders/<int:order_id>/delete")
@admin_required
def admin_order_delete(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    try:
        for item in order.items:
            db.session.delete(item)
        db.session.delete(order)
        db.session.commit()
        flash(f"Đã xóa vĩnh viễn đơn hàng #{order_id} thành công.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Lỗi khi xóa đơn hàng #{order_id}: {e}", "danger")
    return redirect(url_for("admin_orders"))

@app.route("/admin/contacts")
@admin_required
def admin_contacts():
    try:
        Contact.query.filter_by(is_read=False).update({Contact.is_read: True})
        db.session.commit()
    except Exception:
        db.session.rollback()
    contacts = Contact.query.order_by(Contact.created_at.desc()).all()
    return render_template("admin/contacts.html", contacts=contacts)

@app.post("/admin/contacts/<int:contact_id>/delete")
@admin_required
def admin_contact_delete(contact_id):
    c = db.session.get(Contact, contact_id)
    if c:
        db.session.delete(c)
        db.session.commit()
        flash("Đã xóa tin nhắn khỏi danh sách.", "info")
    return redirect(url_for("admin_contacts"))

# ==========================================
# ADMIN LIVE CHAT 2-WAY ROUTES & APIS
# ==========================================
@app.route("/admin/chat")
@admin_required
def admin_chat():
    selected_session = request.args.get("session_id", "").strip()
    return render_template("admin/chat.html", selected_session=selected_session)

@app.get("/admin/api/chat-conversations")
@admin_required
def admin_api_chat_conversations():
    from sqlalchemy import func
    try:
        subquery = db.session.query(
            ChatMessage.session_id,
            func.max(ChatMessage.id).label("max_id")
        ).group_by(ChatMessage.session_id).subquery()

        latest_messages = db.session.query(ChatMessage).join(
            subquery, ChatMessage.id == subquery.c.max_id
        ).order_by(ChatMessage.created_at.desc()).all()

        conversations = []
        for lm in latest_messages:
            unread = ChatMessage.query.filter_by(
                session_id=lm.session_id,
                sender="user",
                is_read_by_admin=False
            ).count()

            user_msg = ChatMessage.query.filter_by(
                session_id=lm.session_id,
                sender="user"
            ).order_by(ChatMessage.id.desc()).first()

            cust_name = user_msg.sender_name if user_msg and user_msg.sender_name else "Khách trực tuyến"
            phone = user_msg.phone if user_msg and user_msg.phone else None

            conversations.append({
                "session_id": lm.session_id,
                "customer_name": cust_name,
                "phone": phone,
                "last_message": lm.message,
                "last_sender": lm.sender,
                "time": lm.created_at.strftime("%H:%M · %d/%m"),
                "unread_count": unread
            })

        return jsonify({"success": True, "conversations": conversations})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.get("/admin/api/chat-thread/<session_id>")
@admin_required
def admin_api_chat_thread(session_id):
    try:
        ChatMessage.query.filter_by(
            session_id=session_id,
            sender="user",
            is_read_by_admin=False
        ).update({ChatMessage.is_read_by_admin: True})
        db.session.commit()

        messages = ChatMessage.query.filter_by(session_id=session_id).order_by(ChatMessage.id.asc()).all()
        cust_phone = None
        cust_name = "Khách trực tuyến"
        for m in messages:
            if m.sender == "user":
                if m.phone:
                    cust_phone = m.phone
                if m.sender_name:
                    cust_name = m.sender_name

        return jsonify({
            "success": True,
            "session_id": session_id,
            "customer_name": cust_name,
            "phone": cust_phone,
            "messages": [{
                "id": m.id,
                "sender": m.sender,
                "sender_name": m.sender_name,
                "message": m.message,
                "time": m.created_at.strftime("%H:%M · %d/%m"),
                "is_read": m.is_read_by_user if m.sender == "admin" else m.is_read_by_admin
            } for m in messages]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.post("/admin/api/chat-reply")
@admin_required
def admin_api_chat_reply():
    data = request.get_json(silent=True) or request.form
    session_id = (data.get("session_id") or "").strip()
    message = (data.get("message") or "").strip()

    if not session_id or not message:
        return jsonify({"success": False, "error": "Thiếu nội dung tin nhắn hoặc phiên chat."}), 400

    admin_user = db.session.get(User, session.get("user_id")) if session.get("user_id") else None
    admin_name = admin_user.name if admin_user else "Kỹ thuật Nhiệm Autotech"

    reply_msg = ChatMessage(
        session_id=session_id,
        sender="admin",
        sender_name=admin_name,
        message=message,
        is_read_by_admin=True,
        is_read_by_user=False
    )
    db.session.add(reply_msg)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": {
            "id": reply_msg.id,
            "sender": "admin",
            "sender_name": admin_name,
            "message": reply_msg.message,
            "time": reply_msg.created_at.strftime("%H:%M · %d/%m")
        }
    })

@app.get("/admin/api/chat-unread-count")
@admin_required
def admin_api_chat_unread_count():
    try:
        count = ChatMessage.query.filter_by(sender="user", is_read_by_admin=False).count()
        return jsonify({"count": count})
    except Exception:
        return jsonify({"count": 0})


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

@app.route("/admin/notifications")
@admin_required
def admin_notifications():
    notifications = Notification.query.order_by(Notification.created_at.desc()).all()
    return render_template("admin/notifications.html", notifications=notifications)

@app.post("/admin/notifications/mark-read")
@admin_required
def admin_notifications_mark_read():
    try:
        Notification.query.filter_by(is_read=False).update({Notification.is_read: True})
        db.session.commit()
        flash("Đã đánh dấu tất cả thông báo là đã đọc.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Lỗi khi đánh dấu thông báo: {e}", "danger")
    return redirect(request.referrer or url_for("admin_notifications"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
