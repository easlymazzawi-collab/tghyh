# Login Tester

Tool test đăng nhập qua **API chính thức** trên domain bạn kiểm soát. Gồm giao diện HTML, backend đa luồng, rate limit và ghi log kết quả.

## Tính năng

- Giao diện web nhập `user:pass` (mỗi dòng) + URL API login
- Chạy nhiều luồng song song (cấu hình 1–10 workers)
- Rate limit (request/giây) để tránh quá tải API
- **Domain allowlist** — chỉ gọi tới domain trong `config.json`
- Log chi tiết từng lần đăng nhập vào `logs/<job_id>.jsonl`
- Mock login API sẵn có để demo trên `localhost`

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Chạy

```bash
python run.py
```

Mở trình duyệt: **http://localhost:8080**

Nhấn **Điền demo** để load URL mock và danh sách tài khoản mẫu.

### Tài khoản demo (mock API)

| Username | Password  |
|----------|-----------|
| admin    | admin123  |
| user1    | pass111   |
| user2    | pass222   |
| demo     | demo2024  |

## Cấu hình domain cho phép

Sửa `config.json`:

```json
{
  "allowed_domains": ["localhost", "127.0.0.1", "staging.yourdomain.com"],
  "default_rate_limit_rps": 2,
  "default_max_workers": 3,
  "max_credentials_per_job": 100,
  "log_dir": "logs"
}
```

Chỉ URL thuộc các domain trên mới được phép test.

## API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/` | Giao diện HTML |
| GET | `/api/config` | Cấu hình hiện tại |
| POST | `/api/jobs` | Bắt đầu job test login |
| GET | `/api/jobs/{job_id}` | Trạng thái & kết quả |
| POST | `/api/discover-form` | Tự phát hiện ô username/password trên trang HTML |
| GET | `/mock/login-page` | Trang login HTML demo |

### Ví dụ request

```bash
curl -X POST http://localhost:8080/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "login_url": "http://localhost:8080/mock/login",
    "credentials": "admin:admin123\nuser1:pass111",
    "max_workers": 2,
    "rate_limit_rps": 2
  }'
```

## Kết quả khi đăng nhập thành công

Mỗi tài khoản thành công trả về:

- HTTP status & thời gian phản hồi (ms)
- JSON response (token, user, message, …)
- Cookie / Authorization header (nếu có)

## Lưu ý bảo mật

- Chỉ dùng trên hệ thống/API **do bạn sở hữu** hoặc có **ủy quyền pentest**
- Không dùng để test credential trên dịch vụ bên thứ ba
- Thêm domain production vào allowlist chỉ khi bạn kiểm soát server đó

## Biến môi trường

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `LOGIN_TESTER_HOST` | `0.0.0.0` | Host bind |
| `LOGIN_TESTER_PORT` | `8080` | Port |
