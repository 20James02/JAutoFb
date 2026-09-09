# Facebook Auto Poster via Chrome Remote Debugging (Playwright)

Bộ công cụ tự động hóa điều khiển Google Chrome để đăng bài lên Facebook mà **không cần nhập mật khẩu trong mã nguồn**, tránh bị Facebook quét checkpoint / khoá tài khoản.

---

## 📁 Thư mục dự án
`D:\Workspace\JAutoFb`

---

## 🚀 Hướng dẫn sử dụng (3 bước đơn giản)

### Bước 1: Khởi động Chrome hỗ trợ Remote Debugging
Nhấp đúp chuột vào file `start_chrome.bat` (hoặc mở cmd/powershell gõ `.\start_chrome.bat`).

* Một cửa sổ trình duyệt Chrome riêng biệt sẽ mở ra với cổng điều khiển `9222`.
* Bạn tiến hành đăng nhập Facebook trên cửa sổ Chrome này (chỉ cần đăng nhập **1 lần duy nhất**, Chrome sẽ lưu cookie/session cho các lần sau).
* **Lưu ý:** Giữ cửa sổ Chrome này mở trong lúc chạy script tự động.

---

### Bước 2: Chuẩn bị nội dung & Chạy script

Mở Terminal (cmd hoặc powershell) tại thư mục này và chạy các lệnh tương ứng:

#### 1. Đăng lên Trang cá nhân (Personal Profile)
```bash
python fb_poster.py --target personal --content "Xin chào cả nhà! Đây là bài viết tự động."
```

#### 2. Đăng kèm một hoặc nhiều hình ảnh / video
```bash
python fb_poster.py --target personal --content "Hình ảnh mới nhất hôm nay" --media "C:\duong_dan\anh1.jpg" "C:\duong_dan\anh2.png"
```

#### 3. Đăng bài vào Nhóm (Group)
Truyền URL hoặc ID của nhóm qua tham số `--url`:
```bash
python fb_poster.py --target group --url "https://www.facebook.com/groups/123456789" --content "Chào các thành viên trong nhóm!"
```

#### 4. Đăng bài lên Fanpage
Truyền URL của Fanpage qua `--url`:
```bash
python fb_poster.py --target page --url "https://www.facebook.com/tenfanpage" --content "Thông báo mới từ Fanpage!"
```

#### 5. Đọc nội dung dài từ file văn bản (.txt)
Nếu bài viết dài, có nhiều dòng hoặc ký tự đặc biệt, hãy lưu vào file `post.txt` và chạy:
```bash
python fb_poster.py --target personal --file-content post.txt --media "banner.png"
```

---

### 🛡️ Chế độ an toàn / Thử nghiệm (`--dry-run`)
Nếu bạn muốn script tự động mở Facebook, soạn bài viết, đính kèm ảnh nhưng **chưa bấm nút "Đăng"** để bạn tự xem lại trên màn hình:
```bash
python fb_poster.py --target personal --content "Kiểm tra thử nghiệm" --dry-run
```
