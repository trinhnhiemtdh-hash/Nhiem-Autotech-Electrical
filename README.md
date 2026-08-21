# Nhiệm Autotech Electrical

Website bán thiết bị điện/tự động hóa, thư viện AutoCAD và quản lý đơn hàng bằng Flask + SQLite.

## 1. Cài đặt

Windows:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Khởi tạo CSDL

```powershell
flask --app app init-db
```

Hoặc chạy:

```powershell
python app.py
```

## 3. Tài khoản Admin mặc định

Email:
`admin@nhiem-autotech.local`

Mật khẩu:
`Admin@123456`

Nên đổi ngay bằng biến môi trường trước khi đưa website lên Internet.

## 4. Chạy website

```powershell
python app.py
```

Mở:
`http://127.0.0.1:5000`

Admin:
`http://127.0.0.1:5000/admin`

## 5. Đưa website lên Google

Khi triển khai, đặt biến môi trường `SITE_URL` thành tên miền HTTPS thật, ví dụ:

```powershell
$env:SITE_URL="https://www.tenmiencuaban.vn"
```

Để bật thanh toán QR xác nhận thủ công, cấu hình thông tin tài khoản nhận tiền:

```powershell
$env:BANK_ID="MB"
$env:BANK_ACCOUNT="0123456789"
$env:BANK_ACCOUNT_NAME="NGUYEN VAN A"
```

Khách tạo đơn, quét QR và chuyển khoản với nội dung `NA<ma-don>`. Trong Admin →
Đơn hàng, đổi trạng thái thành `Đã thanh toán` để mở quyền tải file CAD.

Sau đó xác minh tên miền trong Google Search Console và gửi sitemap tại
`https://www.tenmiencuaban.vn/sitemap.xml`. Không gửi sitemap có địa chỉ
`127.0.0.1` vì Google không truy cập được máy tính của bạn.

## 6. Chức năng hiện có

- Trang chủ thương hiệu Nhiệm Autotech Electrical
- Danh mục thiết bị
- Tìm kiếm sản phẩm
- Chi tiết sản phẩm + thông số kỹ thuật
- Giỏ hàng
- Đăng ký / đăng nhập
- Đặt hàng
- Lịch sử đơn hàng
- Thư viện CAD
- Upload ảnh sản phẩm
- Upload DWG/DXF/PDF/ZIP...
- Khu vực Admin
- Quản lý sản phẩm
- Quản lý đơn hàng
- Quản lý liên hệ

## Lưu ý triển khai thật

Đây là nền tảng MVP chạy được. Trước khi bán hàng thật cần bổ sung:
- CSRF protection
- Phân quyền admin chặt hơn
- HTTPS
- Rate limiting
- Xác thực email / reset mật khẩu
- Cổng thanh toán
- Tính phí vận chuyển
- Sao lưu database
- Virus scanning cho file upload
- Không để file CAD riêng tư trong thư mục public
- PostgreSQL/MySQL khi lượng truy cập tăng
- Biến môi trường cho SECRET_KEY và tài khoản admin
