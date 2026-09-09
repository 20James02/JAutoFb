"""
Facebook Auto Poster via Chrome Remote Debugging (CDP) & Playwright
Supports:
- Targets: Personal Timeline, Groups, Fanpages
- Content: Text, Images, Videos
- Works directly with an existing logged-in Chrome browser via Remote Debugging port (9222).
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import argparse
import os
import time
import urllib.request
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def is_chrome_cdp_ready(port=9222) -> bool:
    """Kiểm tra xem Chrome có đang mở cổng remote debugging hay không"""
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False

class FacebookPoster:
    def __init__(self, port=9222, dry_run=False):
        self.port = port
        self.dry_run = dry_run
        self.browser = None
        self.context = None
        self.page = None

    def connect(self):
        print(f"[*] Đang kết nối tới Chrome tại http://127.0.0.1:{self.port}...")
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        except Exception as e:
            raise ConnectionError(
                f"\n[!] Không thể kết nối tới Chrome trên cổng {self.port}.\n"
                f"Vui lòng chạy file 'start_chrome.bat' trước để mở Chrome với cổng debugging!\n"
                f"Chi tiết lỗi: {e}"
            )

        contexts = self.browser.contexts
        if not contexts:
            raise RuntimeError("Không tìm thấy browser context nào trong Chrome đang mở.")

        self.context = contexts[0]
        pages = self.context.pages
        # Tìm trang facebook sẵn có hoặc dùng tab đầu tiên / mở tab mới
        self.page = None
        for p in pages:
            if "facebook.com" in p.url:
                self.page = p
                print(f"[+] Tìm thấy tab Facebook đang mở: {p.url}")
                break
        
        if not self.page:
            self.page = self.context.new_page()
            print("[+] Đã mở tab mới để điều khiển.")

        self.page.bring_to_front()

    def check_login(self):
        """Kiểm tra xem đã đăng nhập Facebook chưa"""
        self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        time.sleep(2)
        # Nếu có form login
        if self.page.locator('input[name="email"], input[id="email"]').count() > 0:
            print("\n[!] BẠN CHƯA ĐĂNG NHẬP FACEBOOK TRÊN TRÌNH DUYỆT NÀY!")
            print("[!] Hãy đăng nhập Facebook thủ công trên cửa sổ Chrome vừa mở, sau đó chạy lại script.")
            return False
        print("[+] Đã xác nhận trạng thái đăng nhập Facebook thành công.")
        return True

    def open_post_modal(self, target_type="personal", target_url=None):
        """Điều hướng tới trang đích và mở hộp thoại tạo bài viết"""
        if target_type == "personal":
            print("[*] Đang vào trang chủ Facebook...")
            self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            time.sleep(3)
            
            # Click vào "Bạn đang nghĩ gì thế?" / "What's on your mind?"
            triggers = [
                '//span[contains(text(), "Bạn đang nghĩ gì thế")]',
                '//span[contains(text(), "What\'s on your mind")]',
                'div[role="button"]:has-text("Bạn đang nghĩ gì thế")',
                'div[role="button"]:has-text("What\'s on your mind")',
                '//div[@role="button"]//span[contains(., "nghĩ gì")]'
            ]
            clicked = False
            for selector in triggers:
                try:
                    elem = self.page.locator(selector).first
                    if elem.is_visible(timeout=2000):
                        print(f"[+] Đã tìm thấy nút mở soạn bài viết ({selector})")
                        elem.click()
                        clicked = True
                        break
                except Exception:
                    continue
            
            if not clicked:
                # Thử click vào vùng tạo bài viết theo role
                try:
                    self.page.locator('div[data-pagelet="FeedCreation"]').click(timeout=3000)
                    clicked = True
                except Exception:
                    pass

            if not clicked:
                raise RuntimeError("Không thể tìm thấy nút 'Bạn đang nghĩ gì thế?'. Hãy đảm bảo giao diện Facebook đã tải xong.")

        elif target_type == "group":
            if not target_url:
                raise ValueError("Cần cung cấp URL hoặc ID của Nhóm (--url)")
            
            if not target_url.startswith("http"):
                target_url = f"https://www.facebook.com/groups/{target_url}"
            
            print(f"[*] Đang vào nhóm: {target_url} ...")
            self.page.goto(target_url, wait_until="domcontentloaded")
            time.sleep(4)

            triggers = [
                '//span[contains(text(), "Viết gì đó")]',
                '//span[contains(text(), "Write something")]',
                'div[role="button"]:has-text("Viết gì đó")',
                'div[role="button"]:has-text("Write something")',
                '//span[contains(text(), "Tạo bài viết")]',
                '//span[contains(text(), "Create post")]',
            ]
            clicked = False
            for selector in triggers:
                try:
                    elem = self.page.locator(selector).first
                    if elem.is_visible(timeout=3000):
                        print(f"[+] Đã bấm vào nút soạn bài trong nhóm ({selector})")
                        elem.click()
                        clicked = True
                        break
                except Exception:
                    continue
            
            if not clicked:
                raise RuntimeError("Không tìm thấy nút tạo bài viết trong Nhóm. Vui lòng kiểm tra quyền đăng bài của tài khoản.")

        elif target_type == "page":
            if not target_url:
                raise ValueError("Cần cung cấp URL của Fanpage (--url)")
            
            print(f"[*] Đang vào Fanpage: {target_url} ...")
            self.page.goto(target_url, wait_until="domcontentloaded")
            time.sleep(4)

            triggers = [
                '//span[contains(text(), "Tạo bài viết")]',
                '//span[contains(text(), "Create post")]',
                '//span[contains(text(), "Bạn đang nghĩ gì thế")]',
                'div[role="button"]:has-text("Tạo bài viết")',
                'div[role="button"]:has-text("Create post")',
                'div[role="button"]:has-text("Bạn đang nghĩ gì thế")',
            ]
            clicked = False
            for selector in triggers:
                try:
                    elem = self.page.locator(selector).first
                    if elem.is_visible(timeout=3000):
                        print(f"[+] Đã bấm vào nút soạn bài trên Fanpage ({selector})")
                        elem.click()
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                raise RuntimeError("Không tìm thấy nút tạo bài viết trên Fanpage. Hãy đảm bảo bạn đã chuyển sang profile của Page nếu FB yêu cầu.")

        # Chờ hộp thoại soạn thảo (dialog) xuất hiện
        print("[*] Đang chờ hộp thoại tạo bài viết hiển thị...")
        dialog = self.page.locator('div[role="dialog"]').first
        dialog.wait_for(state="visible", timeout=10000)
        print("[+] Hộp thoại tạo bài viết đã hiển thị sẵn sàng.")
        return dialog

    def enter_content(self, dialog, content_text):
        """Nhập nội dung văn bản vào ô soạn thảo"""
        print(f"[*] Đang nhập nội dung bài viết ({len(content_text)} ký tự)...")
        editor = dialog.locator('div[role="textbox"][contenteditable="true"]').first
        editor.wait_for(state="visible", timeout=5000)
        editor.click()
        time.sleep(0.5)

        # Sử dụng keyboard insert_text theo từng dòng kết hợp Shift+Enter để bảo toàn xuống dòng và emoji
        normalized = (content_text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        for i, line in enumerate(lines):
            if line:
                words = line.split(" ")
                for j, word in enumerate(words):
                    self.page.keyboard.insert_text(word + (" " if j < len(words) - 1 else ""))
                    time.sleep(0.02)
            if i < len(lines) - 1:
                self.page.keyboard.press("Shift+Enter")
                time.sleep(0.1)
        time.sleep(1)
        print("[+] Đã nhập nội dung bài viết xong.")

    def upload_media(self, dialog, media_files):
        """Tải lên hình ảnh hoặc video"""
        if not media_files:
            return

        valid_files = []
        for f in media_files:
            p = Path(f).resolve()
            if p.exists():
                valid_files.append(str(p))
            else:
                print(f"[!] Cảnh báo: File không tồn tại: {f}")

        if not valid_files:
            print("[!] Không có file hợp lệ nào để tải lên.")
            return

        print(f"[*] Đang đính kèm {len(valid_files)} file media...")

        photo_btn_selectors = [
            'div[aria-label="Ảnh/video"]',
            'div[aria-label="Photo/video"]',
            'div[aria-label*="Ảnh"]',
            'div[aria-label*="Photo"]',
            '//div[@aria-label="Ảnh/video"]',
            '//div[@aria-label="Photo/video"]'
        ]
        
        file_inputs = dialog.locator('input[type="file"]')
        if file_inputs.count() == 0:
            for s in photo_btn_selectors:
                try:
                    btn = dialog.locator(s).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        time.sleep(1.5)
                        break
                except Exception:
                    continue

        file_input = dialog.locator('input[type="file"]').first
        file_input.wait_for(state="attached", timeout=10000)
        file_input.set_input_files(valid_files)
        print(f"[+] Đã đẩy {len(valid_files)} file vào trình tải lên.")
        
        print("[*] Đang chờ Facebook xử lý ảnh/video tải lên...")
        time.sleep(5)
        print("[+] Hoàn tất đính kèm media.")

    def submit_post(self, dialog):
        """Bấm nút Đăng bài"""
        if self.dry_run:
            print("\n[CHẾ ĐỘ XEM TRƯỚC - DRY RUN]")
            print("[+] Bài viết đã được soạn thảo đầy đủ trên Chrome.")
            print("[+] Bỏ qua bước bấm Đăng vì đang bật --dry-run. Bạn có thể kiểm tra trực tiếp trên Chrome!")
            return True

        print("[*] Đang tìm nút 'Đăng'...")
        post_btn_selectors = [
            'div[aria-label="Đăng"]',
            'div[aria-label="Post"]',
            'div[role="button"]:has-text("Đăng")',
            'div[role="button"]:has-text("Post")',
            '//div[@aria-label="Đăng"]',
            '//div[@aria-label="Post"]'
        ]

        post_btn = None
        for s in post_btn_selectors:
            try:
                btn = dialog.locator(s).first
                if btn.is_visible(timeout=2000):
                    post_btn = btn
                    break
            except Exception:
                continue

        if not post_btn:
            raise RuntimeError("Không tìm thấy nút 'Đăng' trên giao diện.")

        time.sleep(1)
        post_btn.click()
        print("[+] Đã nhấn nút ĐĂNG BÀI!")

        print("[*] Đang chờ xác nhận bài viết được đăng...")
        try:
            dialog.wait_for(state="hidden", timeout=25000)
            print("\n=======================================================")
            print(" [THÀNH CÔNG] Bài viết đã được đăng lên Facebook!")
            print("=======================================================\n")
            return True
        except PlaywrightTimeoutError:
            print("[!] Lưu ý: Dialog mất hơn 25s để đóng. Hãy kiểm tra lại trên Chrome để chắc chắn bài đã đăng.")
            return True

    def run(self, target_type="personal", target_url=None, content="", media_files=None):
        try:
            self.connect()
            if not self.check_login():
                return False

            dialog = self.open_post_modal(target_type=target_type, target_url=target_url)
            
            if content:
                self.enter_content(dialog, content)
            
            if media_files:
                self.upload_media(dialog, media_files)

            success = self.submit_post(dialog)
            return success
        finally:
            if self.browser:
                print("[*] Hoàn thành tác vụ tự động hóa.")


def main():
    parser = argparse.ArgumentParser(description="Facebook Auto Poster via Chrome Remote Debugging")
    parser.add_argument(
        "--target", "-t",
        choices=["personal", "group", "page"],
        default="personal",
        help="Vi tri dang bai: 'personal' (Trang ca nhan), 'group' (Nhom), 'page' (Fanpage)"
    )
    parser.add_argument(
        "--url", "-u",
        dest="target_url",
        default=None,
        help="URL hoac ID cua Nhom hoac Fanpage (bat buoc khi target la group hoac page)"
    )
    parser.add_argument(
        "--content", "-c",
        default="",
        help="Noi dung van ban bai dang (hoac dung --file-content de doc tu file)"
    )
    parser.add_argument(
        "--file-content",
        default=None,
        help="Duong dan file .txt chua noi dung van ban bai dang"
    )
    parser.add_argument(
        "--media", "-m",
        nargs="*",
        default=[],
        help="Danh sach duong dan file anh hoac video can dinh kem (cach nhau boi dau cach)"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=9222,
        help="Cong Chrome Remote Debugging (mac dinh 9222)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Che do thu nghiem: tu dong soan bai va dinh kem anh nhung KHONG bam nut Dang"
    )

    args = parser.parse_args()

    # Kiểm tra cổng debugging
    if not is_chrome_cdp_ready(args.port):
        print(f"\n[LỖI] Không phát hiện thấy Chrome đang mở cổng {args.port}!")
        print("Vui lòng thực hiện các bước sau:")
        print("1. Chạy file 'start_chrome.bat' trong thư mục này để mở Chrome với cổng debugging.")
        print("2. Đăng nhập Facebook trên cửa sổ Chrome đó.")
        print("3. Chạy lại lệnh này.\n")
        sys.exit(1)

    # Đọc nội dung từ file nếu có
    content = args.content
    if args.file_content:
        if os.path.exists(args.file_content):
            with open(args.file_content, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            print(f"[!] Không tìm thấy file nội dung: {args.file_content}")
            sys.exit(1)

    if not content and not args.media:
        print("[LỖI] Bạn phải cung cấp ít nhất nội dung văn bản (--content) hoặc file ảnh/video (--media)!")
        sys.exit(1)

    if args.target in ["group", "page"] and not args.target_url:
        print(f"[LỖI] Khi đăng vào {args.target}, bạn phải cung cấp thêm --url <URL hoặc ID>")
        sys.exit(1)

    poster = FacebookPoster(port=args.port, dry_run=args.dry_run)
    poster.run(
        target_type=args.target,
        target_url=args.target_url,
        content=content,
        media_files=args.media
    )

if __name__ == "__main__":
    main()
