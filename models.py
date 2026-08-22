from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(160), unique=True, nullable=False)
    image = db.Column(db.String(255))

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"))
    category = db.relationship("Category", backref="products")
    name = db.Column(db.String(220), nullable=False)
    slug = db.Column(db.String(240), unique=True, nullable=False)
    sku = db.Column(db.String(80))
    description = db.Column(db.Text)
    specifications = db.Column(db.Text)
    price = db.Column(db.Float, default=0)
    stock = db.Column(db.Integer, default=0)
    image = db.Column(db.String(255))
    images = db.Column(db.Text, default="")
    video_url = db.Column(db.String(300))
    video_file = db.Column(db.String(255))
    cad_file = db.Column(db.String(255))
    status = db.Column(db.String(30), default="active")
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def youtube_embed_url(self):
        if not self.video_url:
            return None
        import re
        url = self.video_url.strip()
        # Youtube watch
        m = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url)
        if m:
            return f"https://www.youtube.com/embed/{m.group(1)}"
        return url

    @property
    def gallery_list(self):
        imgs = []
        if self.image:
            imgs.append(self.image)
        if self.images:
            for piece in self.images.split(","):
                piece = piece.strip()
                if piece and piece not in imgs:
                    imgs.append(piece)
        return imgs

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user = db.relationship("User")
    customer_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(180), nullable=True)
    phone = db.Column(db.String(40), nullable=False)
    address = db.Column(db.String(300), default="Tài liệu số / Bản vẽ CAD")
    note = db.Column(db.Text)
    total = db.Column(db.Float, default=0)
    # statuses: 'pending_payment', 'awaiting_confirmation', 'paid', 'cancelled'
    status = db.Column(db.String(30), default="pending_payment")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship("OrderItem", backref="order", lazy=True, cascade="all, delete-orphan")

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    product_id = db.Column(db.Integer)
    product_name = db.Column(db.String(220))
    price = db.Column(db.Float)
    quantity = db.Column(db.Integer)
    subtotal = db.Column(db.Float)

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(180))
    phone = db.Column(db.String(40))
    message = db.Column(db.Text)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Download(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
