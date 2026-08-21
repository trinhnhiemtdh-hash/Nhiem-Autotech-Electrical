from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from models import db, User, Category, Product, Order, OrderItem, Contact, Download
from config import Config
from functools import wraps
from pathlib import Path
from datetime import timezone
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape
import os, json

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

ALLOWED_DOCS = {"dwg", "dxf", "pdf", "zip", "rar", "xlsx", "docx"}

def public_url(endpoint, **values):
    """Build a canonical public URL, independent of local development host."""
    relative_url = url_for(endpoint, **values)
    if app.config["SITE_URL"]:
        return f"{app.config['SITE_URL']}{relative_url}"
    return url_for(endpoint, _external=True, **values)

@app.after_request
def prevent_private_pages_from_being_indexed(response):
    private_endpoints = {
        "login", "register", "logout", "cart", "cart_add", "cart_update", "cart_remove",
        "checkout", "payment", "account_orders", "cad_download", "admin_dashboard", "admin_products",
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
    featured = Product.query.filter_by(status="active").order_by(Product.created_at.desc()).limit(8).all()
    categories = Category.query.order_by(Category.name).all()
    return render_template("index.html", featured=featured, categories=categories)

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
    products = query.distinct().order_by(Product.created_at.desc()).all()
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

@app.route("/robots.txt")
def robots_txt():
    base_url = app.config["SITE_URL"] or request.url_root.rstrip("/")
    content = f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n"
    return Response(content, mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap_xml():
    pages = [
        "",
        "/products",
        "/cad-library",
        "/thuvien-autocad-thiet-bi-dien",
        "/mach-dieu-khien-plc",
        "/library-by-brand",
        "/panel-forms",
        "/quote",
        "/contact",
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    base_url = app.config["SITE_URL"] or request.url_root.rstrip("/")
    for path in pages:
        xml += "  <url>\n"
        xml += f"    <loc>{escape(base_url + path)}</loc>\n"
        xml += "    <changefreq>weekly</changefreq>\n"
        xml += "    <priority>0.8</priority>\n"
        xml += "  </url>\n"
    for product in Product.query.filter_by(status="active").all():
        lastmod = product.created_at.replace(tzinfo=timezone.utc).date().isoformat()
        xml += "  <url>\n"
        xml += f"    <loc>{escape(base_url + '/product/' + quote(product.slug))}</loc>\n"
        xml += f"    <lastmod>{lastmod}</lastmod>\n"
        xml += "    <changefreq>weekly</changefreq>\n"
        xml += "    <priority>0.7</priority>\n"
        xml += "  </url>\n"
    xml += "</urlset>\n"
    return Response(xml, mimetype="application/xml")

@app.route("/cad-download/<int:product_id>")
def cad_download(product_id):
    product = db.session.get(Product, product_id)
    if not product or not product.cad_file:
        abort(404)
    if not session.get("user_id"):
        flash("Hãy đăng nhập để tải thư viện CAD.", "warning")
        return redirect(url_for("login", next=request.path))
    purchase = (Order.query.join(OrderItem)
                .filter(Order.user_id == session["user_id"], Order.status == "paid",
                        OrderItem.product_id == product.id)
                .first())
    if not purchase:
        flash("Bạn chỉ có thể tải file sau khi đơn hàng đã được xác nhận thanh toán.", "warning")
        return redirect(url_for("account_orders"))
    path = Path(app.config["UPLOAD_FOLDER"]) / "cad"
    file_path = path / product.cad_file
    if not file_path.exists():
        abort(404)
    db.session.add(Download(user_id=session["user_id"], product_id=product.id))
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
    flash("Đã thêm sản phẩm vào giỏ hàng.", "success")
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
    if not session.get("user_id"):
        flash("Vui lòng đăng nhập trước khi đặt hàng.", "warning")
        return redirect(url_for("login", next=url_for("checkout")))
    items, total = cart_items()
    if not items:
        return redirect(url_for("cart"))
    if request.method == "POST":
        order = Order(user_id=session["user_id"], customer_name=request.form["customer_name"],
                      phone=request.form["phone"], address=request.form["address"],
                      note=request.form.get("note",""), total=total, status="pending_payment")
        db.session.add(order)
        db.session.flush()
        for p, qty, subtotal in items:
            db.session.add(OrderItem(order_id=order.id, product_id=p.id, product_name=p.name,
                                     price=p.price, quantity=qty, subtotal=subtotal))
        db.session.commit()
        session["cart"] = {}
        flash(f"Đặt hàng thành công. Mã đơn #{order.id}.", "success")
        return redirect(url_for("payment", order_id=order.id))
    user = db.session.get(User, session["user_id"])
    return render_template("checkout.html", items=items, total=total, user=user)

@app.route("/payment/<int:order_id>")
def payment(order_id):
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.path))
    order = Order.query.filter_by(id=order_id, user_id=session["user_id"]).first_or_404()
    transfer_note = f"NA{order.id}"
    qr_url = None
    if app.config["BANK_ID"] and app.config["BANK_ACCOUNT"]:
        parameters = urlencode({
            "amount": f"{order.total:.0f}",
            "addInfo": transfer_note,
            "accountName": app.config["BANK_ACCOUNT_NAME"],
        })
        qr_url = f"https://img.vietqr.io/image/{quote(app.config['BANK_ID'])}-{quote(app.config['BANK_ACCOUNT'])}-compact2.png?{parameters}"
    return render_template("payment.html", order=order, transfer_note=transfer_note, qr_url=qr_url,
                           bank_id=app.config["BANK_ID"], bank_account=app.config["BANK_ACCOUNT"],
                           bank_account_name=app.config["BANK_ACCOUNT_NAME"])

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
    return render_template("admin/dashboard.html",
                           product_count=Product.query.count(),
                           order_count=Order.query.count(),
                           user_count=User.query.count(),
                           contact_count=Contact.query.filter_by(is_read=False).count())

@app.route("/admin/products")
@admin_required
def admin_products():
    return render_template("admin/products.html", products=Product.query.order_by(Product.created_at.desc()).all())

@app.route("/admin/products/add", methods=["GET","POST"])
@admin_required
def admin_product_add():
    categories = Category.query.all()
    if request.method == "POST":
        name = request.form["name"].strip()
        slug = request.form["slug"].strip().lower()
        price = float(request.form.get("price",0) or 0)
        product = Product(name=name, slug=slug, sku=request.form.get("sku",""),
                          description=request.form.get("description",""),
                          specifications=request.form.get("specifications",""),
                          price=price, stock=int(request.form.get("stock",0) or 0),
                          category_id=int(request.form["category_id"]))
        image = request.files.get("image")
        cad = request.files.get("cad_file")
        if image and image.filename:
            ext = Path(secure_filename(image.filename)).suffix.lower()
            filename = f"{slug}{ext}"
            image.save(Path(app.config["UPLOAD_FOLDER"]) / "images" / filename)
            product.image = filename
        if cad and cad.filename:
            ext = Path(secure_filename(cad.filename)).suffix.lower().lstrip(".")
            if ext not in ALLOWED_DOCS:
                flash("Định dạng CAD/tài liệu chưa được cho phép.", "danger")
                return render_template("admin/product_add.html", categories=categories)
            filename = f"{slug}-{secure_filename(cad.filename)}"
            cad.save(Path(app.config["UPLOAD_FOLDER"]) / "cad" / filename)
            product.cad_file = filename
        db.session.add(product); db.session.commit()
        flash("Đã thêm sản phẩm.", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_add.html", categories=categories)

@app.post("/admin/products/delete/<int:product_id>")
@admin_required
def admin_product_delete(product_id):
    p = db.session.get(Product, product_id)
    if p:
        p.status = "hidden"
        db.session.commit()
    return redirect(url_for("admin_products"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    return render_template("admin/orders.html", orders=Order.query.order_by(Order.created_at.desc()).all())

@app.post("/admin/orders/<int:order_id>/status")
@admin_required
def admin_order_status(order_id):
    order = db.session.get(Order, order_id)
    allowed_statuses = {"pending_payment", "paid", "cancelled"}
    status = request.form.get("status")
    if not order or status not in allowed_statuses:
        abort(400)
    order.status = status
    db.session.commit()
    return redirect(url_for("admin_orders"))

@app.route("/admin/contacts")
@admin_required
def admin_contacts():
    contacts = Contact.query.order_by(Contact.created_at.desc()).all()
    return render_template("admin/contacts.html", contacts=contacts)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
