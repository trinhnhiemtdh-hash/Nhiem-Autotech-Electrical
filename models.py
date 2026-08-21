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
    cad_file = db.Column(db.String(255))
    status = db.Column(db.String(30), default="active")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User")
    customer_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    address = db.Column(db.String(300), nullable=False)
    note = db.Column(db.Text)
    total = db.Column(db.Float, default=0)
    status = db.Column(db.String(30), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
