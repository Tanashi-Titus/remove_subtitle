"""
vmake_client.py
Tự động hoá trang https://vmake.ai/video-watermark-remover bằng Playwright.

Trang là Next.js SPA: ô input[type=file] được JS dựng sau khi tải xong, và khu
vực upload hiển thị các nhãn "Drag / Paste / Click", "Upload", "Batch upload",
"Or import from link". Vì vậy phần upload ở đây:
  1) Chờ trang ổn định (networkidle) rồi DÒ input[type=file] tới khi xuất hiện.
  2) Nếu vẫn không có input, bấm nút "Upload" và bắt hộp chọn file (file chooser).

Phần bấm nút giữa (Text/Caption -> Apply) khó đoán theo thời gian nên mặc định để
CHẾ ĐỘ BÁN TỰ ĐỘNG ("semi"): tool tự upload + tự lưu file tải về, bạn chỉ bấm vài
nút trên cửa sổ trình duyệt. Chế độ "auto" cố bấm hộ (thử nghiệm).

Nếu site đổi giao diện, chỉnh các hằng bên dưới.
"""

import os
import re
import sys
import time


def _use_bundled_browsers():
    """
    Khi chạy bản .exe có NHÚNG Chromium: trỏ Playwright tới trình duyệt đóng gói
    (thư mục `ms-playwright/` trong gói) để máy đích CHƯA cài Chrome vẫn chạy được.
    Không ghi đè nếu người dùng đã tự đặt PLAYWRIGHT_BROWSERS_PATH.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    bdir = os.path.join(base, "ms-playwright")
    if os.path.isdir(bdir):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bdir


UPLOAD_URL = "https://vmake.ai/video-watermark-remover/upload"
EDITOR_URL = "https://vmake.ai/video-watermark-remover/editor"

# Thư mục profile Chrome bền (cookie/lịch sử thật như người dùng — đỡ bị nhận
# diện là automation so với profile trắng mỗi lần).
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".tnt_video_tool", "chrome_profile")

FILE_INPUT_SELECTOR = 'input[type="file"]'

# Nhãn nút (thử lần lượt, không phân biệt hoa thường)
UPLOAD_BUTTON_TEXTS = ["Upload", "Drag", "Click", "Tải lên", "Chọn"]
# Các loại xoá ở tab "Auto" của vmake. Người dùng chọn trên giao diện app;
# loại đang chọn được truyền vào process_videos qua tham số `removal_type`.
REMOVAL_TYPES = ["Smart", "Subtitle", "Watermark", "Passerby"]
# Nút tải MIỄN PHÍ: "Download 5s preview video". Khớp riêng chữ "preview" để
# KHÔNG bấm nhầm "Download full video" (bản trả phí).
DOWNLOAD_PREVIEW_TEXTS = ["preview"]
# Nút đồng ý cookie / popup hay gặp
DISMISS_TEXTS = ["Accept", "Agree", "Got it", "OK", "Đồng ý", "Allow all", "Accept all"]

# User-Agent thật (tránh "HeadlessChrome" khiến vmake dễ chặn khi chạy ẩn).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _build_launch_args(headless):
    """Cờ Chromium: chống 'ngủ' khi cửa sổ mất focus/bị che, ẩn cờ webdriver;
    thêm WebGL phần mềm khi chạy ẩn để vmake xử lý được video."""
    args = [
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        # Thêm IntensiveWakeUpThrottling + occlusion: chống "ngủ" mạnh hơn khi tool
        # bị thu nhỏ / chạy nền (cả ẩn lẫn hiện trình duyệt).
        "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
        "--disable-blink-features=AutomationControlled",
        "--disable-ipc-flooding-protection",
    ]
    if headless:
        args += [
            "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist",
            "--use-gl=angle",
            "--use-angle=swiftshader",
        ]
    return args


def _launch_browser(p, headless, proxy=None):
    """
    Mở trình duyệt — ưu tiên CHROME THẬT (channel='chrome') cho vân tay giống
    người dùng (khó bị vmake nhận diện là bot hơn Chromium đóng gói). Nếu máy
    không có Chrome thì dùng Chromium đóng gói. `proxy` (dict Playwright) nếu có.
    """
    kw = dict(headless=headless, args=_build_launch_args(headless))
    if proxy:
        kw["proxy"] = proxy
    try:
        return p.chromium.launch(channel="chrome", **kw)
    except Exception:
        return p.chromium.launch(**kw)


def _parse_proxy(raw):
    """
    Chuyển 1 dòng proxy thành dict cho Playwright, hoặc None nếu rỗng/sai.
    Hỗ trợ: host:port | host:port:user:pass | scheme://[user:pass@]host:port
    (scheme = http/https/socks5; mặc định http).
    """
    s = (raw or "").strip()
    if not s or s.startswith("#"):
        return None
    scheme = "http"
    user = pwd = None
    if "://" in s:
        sc, s = s.split("://", 1)
        scheme = (sc.strip().lower() or "http")
    if "@" in s:
        cred, s = s.rsplit("@", 1)
        if ":" in cred:
            user, pwd = cred.split(":", 1)
        else:
            user = cred
    parts = s.split(":")
    if user is None and len(parts) == 4:        # host:port:user:pass
        host, port, user, pwd = parts
    elif len(parts) >= 2:
        host, port = parts[0], parts[1]
    else:
        return None
    host, port = host.strip(), port.strip()
    if not host or not port.isdigit():
        return None
    proxy = {"server": f"{scheme}://{host}:{port}"}
    if user:
        proxy["username"] = user
    if pwd:
        proxy["password"] = pwd
    return proxy


def _port_open(host, port, timeout=1.5):
    """Kiểm tra có dịch vụ đang lắng nghe ở host:port (để biết Tor đã chạy chưa)."""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _tor_newnym(control_port=9051, password=None, host="127.0.0.1"):
    """
    Yêu cầu Tor đổi mạch (exit IP mới) qua control port — SIGNAL NEWNYM.
    Hỗ trợ auth: mật khẩu -> null -> cookie (dạng thường). Trả về (ok, thông tin).
    """
    import socket
    import binascii
    import re as _re
    try:
        s = socket.create_connection((host, int(control_port)), timeout=5)
        s.settimeout(5)

        def cmd(b):
            s.sendall(b)
            return s.recv(2048)

        authed = False
        if password:
            authed = b"250" in cmd(f'AUTHENTICATE "{password}"\r\n'.encode())
        if not authed:
            authed = b"250" in cmd(b"AUTHENTICATE\r\n")          # null auth
        if not authed:
            pr = cmd(b"PROTOCOLINFO 1\r\n")                      # cookie auth
            m = _re.search(rb'COOKIEFILE="([^"]+)"', pr)
            if m:
                path = m.group(1).decode("utf-8", "ignore").encode().decode("unicode_escape")
                with open(path, "rb") as f:
                    cookie = binascii.hexlify(f.read()).decode()
                authed = b"250" in cmd(f"AUTHENTICATE {cookie}\r\n".encode())
        if not authed:
            s.close()
            return False, "xác thực control port thất bại (đặt HashedControlPassword hoặc CookieAuthentication)"

        ok = b"250" in cmd(b"SIGNAL NEWNYM\r\n")
        try:
            s.sendall(b"QUIT\r\n")
        except Exception:
            pass
        s.close()
        return ok, ("ok" if ok else "NEWNYM bị từ chối")
    except Exception as e:
        return False, str(e)


def _launch_persistent(p, headless, profile_dir=PROFILE_DIR):
    """
    Mở Chrome THẬT với profile BỀN (persistent) — cookie/lịch sử tích luỹ như một
    người dùng bình thường, giống trình duyệt bạn tự mở hơn là profile trắng tinh
    của automation. Trả về context (context.pages[0] là trang sẵn có).
    """
    os.makedirs(profile_dir, exist_ok=True)
    kw = dict(
        headless=headless,
        args=_build_launch_args(headless),
        accept_downloads=True,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
    )
    if headless:
        kw["user_agent"] = USER_AGENT
    try:
        return p.chromium.launch_persistent_context(profile_dir, channel="chrome", **kw)
    except Exception:
        return p.chromium.launch_persistent_context(profile_dir, **kw)


def _new_context(browser, session_path=None, headless=False):
    """Tạo context; nếu có file session đã lưu thì nạp để GIỮ trạng thái đăng nhập."""
    kw = dict(
        accept_downloads=True,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
    )
    # Chỉ ÉP User-Agent khi chạy ẩn (Chromium ẩn có UA 'HeadlessChrome' dễ bị chặn).
    # Khi chạy hiện bằng Chrome thật, để UA GỐC cho khớp vân tay (tránh lệch version).
    if headless:
        kw["user_agent"] = USER_AGENT
    if session_path and os.path.exists(session_path):
        kw["storage_state"] = session_path
    return browser.new_context(**kw)


# JS ghi đè Visibility API: ép trang LUÔN báo "visible"/"focused" và NUỐT sự kiện
# visibilitychange/blur. vmake xử lý video phía client bằng requestAnimationFrame/
# WebGL — mặc định Chrome DỪNG rAF khi tab ẩn/minimize -> % xử lý đứng im, phải
# ngồi nhìn mới chạy. Patch này (kèm CDP focus emulation) khiến chạy nền vẫn chạy.
_VISIBILITY_PATCH = r"""
(() => {
  try {
    Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
    Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible', configurable: true});
    Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
    Object.defineProperty(document, 'webkitHidden', {get: () => false, configurable: true});
    document.hasFocus = () => true;
    const swallow = (e) => { e.stopImmediatePropagation(); };
    for (const ev of ['visibilitychange', 'webkitvisibilitychange', 'blur']) {
      window.addEventListener(ev, swallow, true);
      document.addEventListener(ev, swallow, true);
    }
  } catch (e) {}
})();
"""


def _harden_page(context, page, log):
    """
    Chống Chrome "bóp" trang khi cửa sổ mất focus / bị minimize / bị che.

    Các cờ launch chỉ trị timer-throttling & occlusion; chúng KHÔNG ép được
    document.visibilityState -> khi minimize, trang thành 'hidden' và
    requestAnimationFrame ngừng, làm vmake đứng % xử lý. Ở đây:
      1) Nạp init-script ghi đè Visibility API (chạy trước mọi lần điều hướng).
      2) CDP Emulation.setFocusEmulationEnabled -> renderer coi trang LUÔN focus
         (giữ rAF chạy nền). Page.setWebLifecycleState('active') chống freeze.
    Nhờ vậy KHÔNG cần ngồi nhìn cửa sổ, xử lý vẫn chạy đúng tốc độ.
    """
    try:
        page.add_init_script(_VISIBILITY_PATCH)
    except Exception:
        pass
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        try:
            cdp.send("Page.setWebLifecycleState", {"state": "active"})
        except Exception:
            pass
        log("  Đã bật chống-throttle (focus emulation) — minimize/chạy nền vẫn xử lý.")
    except Exception as e:
        log(f"  (Không bật được focus emulation, có thể phải để cửa sổ hiện: {e})")


def _wait_dom_ready(page, timeout_ms=15000):
    """
    Chờ SPA dựng xong ô upload — THAY cho networkidle. vmake là Next.js có
    websocket/analytics nên networkidle gần như không bao giờ đạt -> luôn chờ phí
    trọn 20s mỗi lần mở trang. Chờ đúng selector nhanh & chắc hơn nhiều.
    """
    try:
        page.wait_for_selector(FILE_INPUT_SELECTOR, state="attached", timeout=timeout_ms)
    except Exception:
        pass


def _interruptible_sleep(seconds, should_stop, log=None, label=None):
    if seconds <= 0:
        return
    if log and label:
        log(f"  {label} {seconds:.1f}s…")
    end = time.time() + seconds
    while time.time() < end:
        if should_stop():
            return
        time.sleep(0.2)


def _dismiss_overlays(page, log):
    """Bấm tắt banner cookie/popup nếu có (không bắt buộc)."""
    for t in DISMISS_TEXTS:
        try:
            loc = page.get_by_role("button", name=re.compile(rf"^{re.escape(t)}$", re.I))
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=2000)
                log(f"  Đã đóng popup: {t}")
                return
        except Exception:
            pass


def _click_first_text(page, texts, log, timeout_ms=8000):
    """Thử bấm phần tử đầu tiên khớp 1 trong các chuỗi (button role hoặc text)."""
    for t in texts:
        pat = re.compile(rf"\b{re.escape(t)}\b", re.I)
        try:
            loc = page.get_by_role("button", name=pat)
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                log(f"  Đã bấm nút: {t}")
                return True
        except Exception:
            pass
        try:
            loc = page.get_by_text(pat)
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                log(f"  Đã bấm: {t}")
                return True
        except Exception:
            pass
    return False


def _upload_via_input(page, src, log, should_stop, timeout=25):
    """Dò input[type=file] (kể cả ẩn) rồi set file trực tiếp."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if should_stop():
            return False
        try:
            if page.locator(FILE_INPUT_SELECTOR).count() > 0:
                page.locator(FILE_INPUT_SELECTOR).first.set_input_files(src)
                log("  Đã nạp file vào input[type=file].")
                return True
        except Exception:
            pass
        page.wait_for_timeout(500)
    return False


def _upload_via_button(page, src, log):
    """Bấm nút Upload và bắt hộp chọn file (file chooser)."""
    for label in UPLOAD_BUTTON_TEXTS:
        try:
            target = page.get_by_text(re.compile(rf"\b{re.escape(label)}\b", re.I)).first
            if target.count() == 0:
                continue
            with page.expect_file_chooser(timeout=8000) as fc:
                target.click()
            fc.value.set_files(src)
            log(f"  Đã upload qua nút '{label}'.")
            return True
        except Exception:
            continue
    return False


def _clear_existing_tasks(page, log, max_clear=12):
    """
    XOÁ HẾT task cũ còn sót trong phiên (do đăng nhập giữ lại, hoặc lần trước lỗi
    nhưng vmake vẫn xử lý xong ở server) TRƯỚC khi upload đoạn mới.

    Nếu không dọn, có thể tồn tại 2 task: cái cũ đã xong (nút Download sẵn) và cái
    mới chưa xong -> tool vớ nhầm nút Download của cái cũ -> tải sai. Dọn sạch để
    chỉ còn đúng 1 task của đoạn đang xử lý.
    """
    cleared = 0
    for _ in range(max_clear):
        try:
            loc = page.locator('div[class*="close-btn"]:has([class*="vmake-delete-icon"])')
            if loc.count() == 0 or not loc.first.is_visible():
                break
            loc.first.click(timeout=2500)
            cleared += 1
            page.wait_for_timeout(700)
            _click_first_text(page, ["Delete", "Confirm", "Yes", "Xoá", "Xóa"],
                              log, timeout_ms=1200)
            page.wait_for_timeout(400)
        except Exception:
            break
    if cleared:
        log(f"  Đã dọn {cleared} task cũ còn sót trước khi upload.")
    return cleared


def _upload(page, src, log, should_stop, first=True):
    """
    Nạp 1 đoạn lên vmake (cùng MỘT phiên trình duyệt cho mọi đoạn).

    first=True : đoạn đầu — mở trang upload rồi nạp file.
    first=False: đoạn kế tiếp — vmake đang ở editor và có sẵn nút "Upload" để
                 nạp video mới, nên ưu tiên bấm nút đó (thay video cũ).
    Trả về True nếu thành công.
    """
    if first:
        page.goto(UPLOAD_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        _dismiss_overlays(page, log)
        # Chờ phiên đăng nhập khôi phục task cũ (nếu có) rồi DỌN SẠCH trước khi up.
        page.wait_for_timeout(1200)
        _clear_existing_tasks(page, log)
        # Trang upload: ô input đáng tin cậy hơn, thử trước rồi tới nút.
        if _upload_via_input(page, src, log, should_stop):
            return True
        if _upload_via_button(page, src, log):
            return True
        raise RuntimeError(
            "Không tìm thấy ô upload trên trang vmake (selector có thể đã đổi)."
        )

    # Đoạn kế tiếp: dùng nút Upload trên editor để nạp video mới.
    _dismiss_overlays(page, log)
    if _upload_via_button(page, src, log):
        return True
    if _upload_via_input(page, src, log, should_stop, timeout=10):
        return True
    # Không thấy nút trên editor -> quay lại mở trang upload như đoạn đầu.
    log("  Không thấy nút Upload trên editor — mở lại trang upload.")
    return _upload(page, src, log, should_stop, first=True)


def _try_auto_flow(page, log, removal_type="Smart"):
    """
    Chọn loại xoá ở tab 'Auto' (Smart/Subtitle/Watermark/Passerby).

    Giao diện vmake KHÔNG có nút Apply riêng — chọn loại xong là bấm thẳng
    'Download ... preview' (xử lý nằm ở bước _auto_download). Vì các thẻ chọn
    render hơi chậm, chờ thẻ hiện rồi mới bấm; khớp đúng tên (anchored) để khỏi
    bấm nhầm chữ khác trên trang (ví dụ 'Remove'/'Remover').
    """
    page.wait_for_timeout(1500)
    # vmake mặc định đã chọn sẵn "Smart" -> không cần bấm gì.
    if str(removal_type).strip().lower() == "smart":
        log("  'Smart' đã được chọn sẵn — không cần bấm.")
        return
    pat = re.compile(rf"^\s*{re.escape(removal_type)}\s*$", re.I)
    try:
        loc = page.get_by_text(pat).first
        loc.wait_for(state="visible", timeout=12000)
        loc.click(timeout=5000)
        log(f"  Đã chọn loại xoá: {removal_type}")
    except Exception:
        log(f"  (Không tìm thấy mục '{removal_type}' — dùng mặc định Smart)")


def _find_preview_button(page):
    """
    Tìm nút tải BẢN PREVIEW (free): "Download 5s preview video".

    Duyệt mọi element có chữ "download": bỏ nút "full video" (trả phí), ưu tiên
    nút có "preview", chỉ lấy element đang HIỂN THỊ. Trả về element hoặc None.
    """
    try:
        loc = page.get_by_text(re.compile(r"download", re.I))
        n = loc.count()
    except Exception:
        return None

    fallback = None
    for i in range(n):
        try:
            el = loc.nth(i)
            if not el.is_visible():
                continue
            txt = (el.inner_text() or "").lower()
        except Exception:
            continue
        if "full" in txt:          # bỏ nút trả phí "Download full video"
            continue
        if "preview" in txt:       # đúng nút preview (free)
            return el
        if fallback is None:       # dự phòng: nút "download" đầu tiên không phải full
            fallback = el
    return fallback


def _click_download_preview(page, log, timeout_ms=4000):
    """Bấm nút tải bản preview (free). Trả về True nếu bấm được."""
    el = _find_preview_button(page)
    if el is None:
        return False
    try:
        el.click(timeout=timeout_ms)
        log("  Đã bấm nút Download (preview).")
        return True
    except Exception:
        return False


def _valid_video(path):
    """File tải về có hợp lệ không (không rỗng/hỏng). Dùng ffprobe nếu có."""
    try:
        if os.path.getsize(path) < 2048:   # < 2KB chắc chắn hỏng/rỗng
            return False
    except OSError:
        return False
    import shutil
    import subprocess
    try:
        from video_utils import find_binary
        ff = find_binary("ffprobe")           # ưu tiên ffprobe đóng gói trong .exe
    except Exception:
        ff = shutil.which("ffprobe")
    if not ff:
        return True   # không có ffprobe -> tạm chấp nhận theo dung lượng
    try:
        out = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return float((out.stdout or "0").strip() or 0) > 0.2
    except Exception:
        return True   # ffprobe trục trặc -> không chặn oan


def _auto_download(page, dst, log, timeout, should_stop):
    """
    Tải bản preview (free) và CHỜ TẢI XONG mới trả về.

    Bước 1: chờ vmake xử lý xong tới khi nút 'Download ... preview' HIỆN (đoạn
            ngắn thường ~20s). Nếu sau ~90s vẫn chưa hiện -> coi như vmake không
            xử lý (nhiều khả năng hết lượt free theo IP) và báo lỗi sớm.
    Bước 2: bấm nút trong MỘT cửa sổ expect_download dài để chắc chắn bắt được
            sự kiện tải và đợi file tải xong (không bị lỡ như cách cũ).
    """
    deadline = time.time() + timeout
    log("  Chờ vmake xử lý & nút 'Download preview' hiện…")
    proc_cap = min(deadline, time.time() + 90)
    while time.time() < proc_cap:
        if should_stop():
            return
        if _find_preview_button(page) is not None:
            break
        _interruptible_sleep(2.0, should_stop)
    else:
        raise RuntimeError(
            "Không thấy nút 'Download preview' (vmake chưa xử lý — có thể đã hết "
            "lượt free theo IP)."
        )

    # Đã thấy nút -> bấm và chờ TẢI XONG trong 1 cửa sổ duy nhất.
    log("  Đã thấy nút Download — bấm và chờ tải về xong…")
    remaining_ms = int(max(15, deadline - time.time()) * 1000)
    with page.expect_download(timeout=remaining_ms) as dl_info:
        if not _click_download_preview(page, log):
            raise RuntimeError("Bấm nút 'Download preview' thất bại.")
    download = dl_info.value
    download.save_as(dst)
    if not _valid_video(dst):
        try:
            os.remove(dst)   # xoá file hỏng để đoạn này được up & xử lý lại
        except OSError:
            pass
        raise RuntimeError("File tải về rỗng/hỏng — sẽ đổi IP & xử lý lại đoạn này.")
    log(f"  Đã tải về: {os.path.basename(dst)}")


def _delete_task(page, log, fname=None):
    """
    Sau khi tải xong: XOÁ task trên vmake để lần mở trình duyệt sau KHÔNG nạp lại
    video cũ đã xử lý (nguyên nhân gây lặp lại đoạn cũ khi dùng profile bền).

    Nút xoá ("delete the task") hiện khi rê chuột vào tên file. Sau khi xoá, trang
    quay lại màn upload ban đầu.
    """
    # Rê chuột vào tên file để chắc nút xoá hiện.
    if fname:
        try:
            nm = page.get_by_text(fname, exact=False).first
            if nm.count() > 0:
                nm.scroll_into_view_if_needed(timeout=2000)
                nm.hover(timeout=2500)
                page.wait_for_timeout(400)
        except Exception:
            pass

    # Nút xoá là ICON trong <div class="...close-btn"><span class="vmake-delete-icon">.
    # Bấm vào DIV CHA (handler nằm ở div) cho chắc; ưu tiên div chứa icon xoá.
    selectors = [
        'div[class*="close-btn"]:has([class*="vmake-delete-icon"])',
        '[class*="vmake-delete-icon"]',
        'div[class*="close-btn"]',
    ]
    btn = None
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                btn = loc.first
                break
        except Exception:
            continue
    if btn is None:
        log("  (Không thấy nút xoá task — bỏ qua)")
        return False

    def _task_gone():
        try:
            if fname:
                return page.get_by_text(fname, exact=False).count() == 0
        except Exception:
            pass
        return False

    # Thử lần lượt: click thường -> click force -> dispatch JS; KIỂM CHỨNG đã xoá.
    for how in ("click", "force", "js"):
        try:
            if how == "click":
                btn.hover(timeout=2000)
                btn.click(timeout=3000)
            elif how == "force":
                btn.click(force=True, timeout=3000)
            else:
                btn.dispatch_event("click")
        except Exception:
            continue
        page.wait_for_timeout(900)
        # Có thể hiện hộp xác nhận -> đồng ý.
        _click_first_text(page, ["Delete", "Confirm", "Yes", "Xoá", "Xóa"], log, timeout_ms=1500)
        page.wait_for_timeout(500)
        if _task_gone():
            log("  Đã xoá task (về màn upload).")
            return True

    log("  (Đã thử bấm xoá nhưng task có vẻ vẫn còn — báo lại để chỉnh selector)")
    return False


def _process_one(page, src, dst, mode, removal_type, log, timeout, should_stop, first=True):
    if not _upload(page, src, log, should_stop, first=first):
        return
    log("  Upload xong, chờ trang dựng trình sửa…")
    # Xác nhận editor đã nạp ĐÚNG đoạn vừa upload (tránh tải nhầm đoạn cũ).
    fname = os.path.basename(src)
    try:
        page.get_by_text(fname).first.wait_for(timeout=15000)
        log(f"  Trình sửa đã nạp đúng file: {fname}")
    except Exception:
        # Editor không hiện đúng đoạn vừa up -> nhiều khả năng nạp nhầm video cũ
        # (carryover) hoặc upload lỗi. Báo lỗi để vòng ngoài ĐỔI IP & xử lý lại
        # đúng đoạn này (không tải nhầm video cũ).
        raise RuntimeError(
            f"Editor không hiện đúng file '{fname}' (nghi nạp nhầm video cũ / "
            "upload lỗi) — sẽ đổi IP & xử lý lại đoạn này."
        )
    _dismiss_overlays(page, log)

    if mode == "auto":
        _try_auto_flow(page, log, removal_type)
        _auto_download(page, dst, log, timeout, should_stop)
        # Tải xong -> xoá task để lần sau không bị nạp lại video cũ đã xử lý.
        if os.path.exists(dst):
            _delete_task(page, log, fname)
    else:
        # Bán tự động: người dùng tự bấm Text/Caption -> Apply -> Download.
        log("  >>> Trên trình duyệt: chọn Text/Caption -> Apply -> Download")
        with page.expect_download(timeout=int(timeout * 1000)) as dl_info:
            pass
        download = dl_info.value
        download.save_as(dst)


def open_login(session_path, should_finish, log=print):
    """
    Mở trình duyệt cho người dùng TỰ đăng nhập vmake, rồi LƯU session ra
    `session_path` để các lần sau khỏi đăng nhập lại.

    should_finish(): hàm trả True khi người dùng bấm 'Tôi đã đăng nhập xong'.
    Trả về True nếu lưu được session.
    """
    _use_bundled_browsers()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError(
            "Chưa cài Playwright. Chạy: pip install playwright && playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=False)
        context = _new_context(browser, session_path, headless=False)  # nạp session cũ nếu có
        page = context.new_page()
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            log("Hãy ĐĂNG NHẬP trên cửa sổ trình duyệt vừa mở (nút tài khoản góc phải).")
            log("Xong rồi bấm nút 'Tôi đã đăng nhập xong' trong app để lưu phiên.")
            # Chờ tới khi người dùng xác nhận đã đăng nhập (hoặc đóng cửa sổ).
            while not should_finish():
                if page.is_closed():
                    break
                page.wait_for_timeout(300)
            os.makedirs(os.path.dirname(session_path), exist_ok=True)
            context.storage_state(path=session_path)
            log(f"Đã lưu phiên đăng nhập: {session_path}")
            return True
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# BATCH: xử lý nhiều video SONG SONG trong cùng 1 phiên (login = không giới hạn).
# Mỗi video là một <div class="task-card--...">: trong card có TÊN file, nút xoá
# (close-btn) và nút "Download 5s preview video" (CHỈ hiện khi xử lý xong; lúc đang
# xử lý hiện "Processing… %").
# ---------------------------------------------------------------------------
# PHẢI dùng "task-card--" (2 gạch) để chỉ khớp CARD THẬT (vd "task-card--Y5yr0"),
# KHÔNG dính "task-card-layout--" (div layout bên trong card, không có nút xoá).
TASK_CARD_SELECTOR = 'div[class*="task-card--"]'

# Ô upload ĐƠN — ô DUY NHẤT dùng được ở luồng MIỄN PHÍ.
# Trang có 3 ô input[type=file]: 1 ô đơn (multiple=false) + 2 ô "Batch upload"
# (multiple=true, đang ẩn). TUYỆT ĐỐI không nạp file vào ô [multiple]: nó nhảy
# sang trang trả phí /batch-upload ("Plus 3 file, Pro 30 file") và XOÁ SẠCH task
# đang xử lý. Vì vậy luôn khoá selector bằng :not([multiple]) thay vì .first.
SINGLE_FILE_INPUT_SELECTOR = 'input[type="file"]:not([multiple])'

# vmake DỰNG LẠI ô input sau mỗi lần up thành công. Nếu lần nạp kế rơi trúng lúc
# đó, file bị NUỐT IM LẶNG: set_input_files vẫn "thành công", nhưng không có card
# nào sinh ra -> chính là lỗi "không thấy card '...' sau khi up". Cách trị: nạp
# xong XÁC NHẬN có card mới (card hiện sau ~0.5s), chưa có thì nạp lại ngay.
UPLOAD_CONFIRM_S = 2.0   # cửa sổ chờ card mới (đo thực tế: 0.4–0.7s)
UPLOAD_TRIES = 4         # đo thực tế: lần 2 luôn ăn


def _wait_new_card(page, before, timeout=UPLOAD_CONFIRM_S):
    """
    Chờ có CARD MỚI so với `before` (số card trước khi nạp). Trả về card mới —
    vmake CHÈN card mới lên ĐẦU danh sách — hoặc None nếu file bị nuốt.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(150)
        cards = _leaf_cards(page)
        if len(cards) > before:
            return cards[0]
    return None


def _leaf_cards(page):
    """
    Danh sách CARD LÁ (mỗi video 1 cái) — phần tử task-card KHÔNG chứa task-card con.
    Lọc bỏ thẻ CHA/wrapper (bọc nhiều card) để không bấm/đối chiếu nhầm.
    """
    out = []
    try:
        loc = page.locator(TASK_CARD_SELECTOR)
        n = loc.count()
    except Exception:
        return out
    for i in range(n):
        c = loc.nth(i)
        try:
            if c.locator(TASK_CARD_SELECTOR).count() == 0:   # không có card con -> card lá
                out.append(c)
        except Exception:
            continue
    return out


def _leaf_card_by_name(page, name):
    """Card LÁ có tên file `name` trong text (hoặc None)."""
    for card in _leaf_cards(page):
        try:
            if name in (card.inner_text() or ""):
                return card
        except Exception:
            continue
    return None


def _wait_task_card(page, name, timeout=45):
    """
    Chờ CARD LÁ mang đúng TÊN xuất hiện (nhãn tên render trễ, có thể ~15–20s sau
    khi card hiện). Chỉ cần khi loại xoá ≠ Smart — lúc đó phải bấm đúng card để
    chọn loại + Apply. Việc CÓ lên hay không đã do _batch_upload xác nhận bằng
    card mới, nên ở đây không đoán mò theo số lượng nữa.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        c = _leaf_card_by_name(page, name)
        if c is not None:
            return c
        page.wait_for_timeout(300)
    return None


def _card_preview_button(card):
    """Nút 'Download ... preview' (free) bên TRONG card; None nếu chưa xong."""
    try:
        loc = card.get_by_text(re.compile(r"preview", re.I))
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible() and "full" not in (el.inner_text() or "").lower():
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


_CONFIRM_TEXTS = ["Delete", "Confirm", "Yes", "OK", "Remove",
                  "Xoá", "Xóa", "Đồng ý", "确认", "确定", "删除", "是"]


def _delete_card(page, card, log, name=None):
    """
    Xoá 1 card — RÊ CHUỘT vào card (nút × thường chỉ bấm được khi hover), thử nhiều
    cách bấm + xác nhận hộp thoại, KIỂM CHỨNG đã biến mất (theo tên hoặc đếm card).
    """
    try:
        before = len(_leaf_cards(page))
    except Exception:
        before = None
    # Rê chuột vào card để hiện/kích hoạt nút ×.
    try:
        card.scroll_into_view_if_needed(timeout=1500)
        card.hover(timeout=1500)
        page.wait_for_timeout(250)
    except Exception:
        pass
    try:
        cb = card.locator('div[class*="close-btn"]').first
        if cb.count() == 0:
            cb = card.locator('[class*="vmake-delete-icon"]').first
        if cb.count() == 0:
            return False
    except Exception:
        return False

    for how in ("click", "force", "js"):
        try:
            try:
                cb.hover(timeout=1000)
            except Exception:
                pass
            if how == "click":
                cb.click(timeout=3000)
            elif how == "force":
                cb.click(force=True, timeout=3000)
            else:
                cb.dispatch_event("click")
        except Exception:
            continue
        # vmake xoá NGAY (không hộp xác nhận) -> chờ chút rồi kiểm chứng đã mất.
        page.wait_for_timeout(500)
        if name is not None:
            if _leaf_card_by_name(page, name) is None:
                return True
        elif before is not None:
            if len(_leaf_cards(page)) < before:
                return True
    return False


def _clear_all_task_cards(page, log, max_clear=80):
    """Xoá SẠCH mọi CARD LÁ cũ còn sót TRƯỚC khi bắt đầu. Trả về số card còn lại."""
    cleared = 0
    stuck = 0
    for _ in range(max_clear):
        leaves = _leaf_cards(page)
        before = len(leaves)
        if before == 0:
            break
        _delete_card(page, leaves[0], log)
        page.wait_for_timeout(300)
        if len(_leaf_cards(page)) >= before:   # không giảm -> kẹt
            stuck += 1
            if stuck >= 3:
                break
        else:
            stuck = 0
            cleared += 1
    left = len(_leaf_cards(page))
    if cleared:
        log(f"  Đã xoá sạch {cleared} task cũ.")
    log(f"  Số task còn lại trước khi up: {left}")
    if left > 0:
        log("  (CẢNH BÁO: chưa xoá hết task cũ — có thể nút xoá đổi giao diện)")
    return left


def _apply_type_in_card(page, card, removal_type, log):
    """Loại ≠ Smart: chọn thẻ loại TRONG card rồi bấm 'Apply' để bắt đầu xử lý."""
    pat = re.compile(rf"^\s*{re.escape(removal_type)}\s*$", re.I)
    try:
        t = card.get_by_text(pat).first
        t.wait_for(state="visible", timeout=8000)
        t.click(timeout=4000)
        log(f"  Chọn loại '{removal_type}' trong card.")
    except Exception:
        log(f"  (Không thấy thẻ '{removal_type}' trong card — bỏ qua)")
    page.wait_for_timeout(500)
    # Nút 'Apply' render sau khi chọn loại -> chờ tới khi hiện rồi bấm (thử vài lần).
    applied = False
    for _ in range(6):
        try:
            ap = card.get_by_role("button", name=re.compile(r"\bApply\b", re.I)).first
            if ap.count() == 0:
                ap = card.get_by_text(re.compile(r"\bApply\b", re.I)).first
            if ap.count() > 0 and ap.is_visible():
                ap.click(timeout=4000)
                log("  Đã bấm Apply.")
                applied = True
                break
        except Exception:
            pass
        page.wait_for_timeout(800)
    if not applied:
        log("  (Không thấy nút Apply trong card)")


def _batch_upload(page, src, log):
    """
    Up 1 đoạn vào phiên bằng cách nạp THẲNG vào ô upload đơn (set_input_files) —
    KHÔNG bấm nút '+ Upload' rồi bắt hộp chọn file như trước: đường đó chậm (mỗi
    cú trượt tốn 8s chờ chooser) và hay trượt, khi trượt lại rơi xuống nhánh dự
    phòng nạp file vào ô đã chết -> đoạn không bao giờ lên -> "không thấy card".

    Nạp xong PHẢI thấy card mới thì mới tính là lên; bị nuốt thì nạp lại ngay.
    Trả về card vừa tạo. Ném lỗi nếu nuốt hết `UPLOAD_TRIES` lần.
    """
    name = os.path.basename(src)
    for attempt in range(1, UPLOAD_TRIES + 1):
        loc = page.locator(SINGLE_FILE_INPUT_SELECTOR)
        try:
            gone = loc.count() == 0
        except Exception:
            gone = True
        if gone:
            # Mất ô upload (trang bị lạc sang chỗ khác) -> mở lại trang upload.
            log("  Không thấy ô upload — mở lại trang upload.")
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            _wait_dom_ready(page)
            _dismiss_overlays(page, log)
            loc = page.locator(SINGLE_FILE_INPUT_SELECTOR)

        before = len(_leaf_cards(page))
        try:
            loc.first.set_input_files(src, timeout=8000)
        except Exception as e:
            log(f"  (nạp '{name}' lỗi lần {attempt}/{UPLOAD_TRIES}: {str(e)[:70]})")
            page.wait_for_timeout(500)
            continue

        card = _wait_new_card(page, before)
        if card is not None:
            return card
        if attempt < UPLOAD_TRIES:
            log(f"  (vmake nuốt '{name}' lần {attempt} — nạp lại)")
    raise RuntimeError(f"vmake nuốt file, không tạo card sau {UPLOAD_TRIES} lần nạp")


def _download_card(page, card, dst, log, timeout_ms=120000):
    """Tải bản preview của ĐÚNG card này về dst. Trả về tên file gốc vmake gợi ý."""
    btn = _card_preview_button(card)
    if btn is None:
        raise RuntimeError("card chưa có nút Download preview")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with page.expect_download(timeout=timeout_ms) as dl:
        btn.click(timeout=8000)
    download = dl.value
    download.save_as(dst)
    try:
        return download.suggested_filename or ""
    except Exception:
        return ""


def _card_match_name(card, names, log=None):
    """
    Đọc TEXT trong 1 card, trả về tên đoạn (đang chờ) khớp DUY NHẤT trong card đó.
    Nếu khớp >1 (selector card quá rộng / nhầm wrapper) -> trả None + cảnh báo để
    KHÔNG tải nhầm.
    """
    try:
        txt = card.inner_text() or ""
    except Exception:
        return None
    hits = [nm for nm in names if nm in txt]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1 and log:
        log(f"  (CẢNH BÁO: 1 card chứa nhiều tên {hits} — bỏ qua, tránh tải nhầm)")
    return None


def _handle_one_done_card(page, in_flight, log):
    """
    Tìm MỘT card ĐÃ XONG (có nút Download), đọc TÊN ngay trong card đó, tải về ĐÚNG
    dst của tên đó rồi xoá card. Xử lý từng card một để khỏi lệch chỉ số khi xoá.
    Trả về tên đã xử lý, hoặc None nếu chưa có card nào xong.
    """
    names = list(in_flight.keys())
    for card in _leaf_cards(page):
        try:
            if _card_preview_button(card) is None:
                continue   # card này chưa xong (đang Processing…)
        except Exception:
            continue
        matched = _card_match_name(card, names, log)
        if matched is None:
            continue
        info = in_flight[matched]
        try:
            sug = _download_card(page, card, info["dst"], log)
        except Exception as e:
            log(f"  Lỗi tải card '{matched}': {e}")
            return None
        if os.path.exists(info["dst"]) and _valid_video(info["dst"]):
            _delete_card(page, card, log, name=matched)   # xoá + kiểm chứng đã mất
            del in_flight[matched]
            log(f"  [✓] '{matched}' -> {os.path.basename(info['dst'])} (vmake gửi: {sug})")
            return matched
        # tải hỏng -> xoá file để poll sau thử lại
        try:
            if os.path.exists(info["dst"]):
                os.remove(info["dst"])
        except OSError:
            pass
        return None
    return None


def _run_batch(page, pending, removal_type, batch_size,
               per_video_timeout, delay_min, delay_max, log, should_stop):
    """
    Pipeline batch: giữ tối đa `batch_size` video xử lý song song. Cái nào XONG
    (card hiện nút Download preview) thì tải ĐÚNG theo tên -> xoá card -> up cái kế.
    Vì đối chiếu theo TÊN nên ghép nối không sai thứ tự. Trả về số đoạn xong.
    """
    import random
    is_smart = str(removal_type).strip().lower() == "smart"
    MAX_ATTEMPTS = 3
    attempts = {}
    in_flight = {}   # name -> {"src","dst","t0"}
    failed = []      # đoạn lỗi quá số lần -> BỎ QUA (không dừng cả mẻ)
    done = 0

    while (pending or in_flight) and not should_stop():
        # 1) Up bù cho đủ batch_size.
        while len(in_flight) < batch_size and pending and not should_stop():
            src, dst = pending.pop(0)
            name = os.path.basename(src)
            try:
                # Đã xác nhận có card mới -> đoạn CHẮC CHẮN đã lên.
                _batch_upload(page, src, log)
                if not is_smart:
                    # Loại ≠ Smart phải bấm đúng card -> chờ nhãn tên hiện.
                    card = _wait_task_card(page, name, timeout=45)
                    if card is None:
                        raise RuntimeError(f"không thấy tên '{name}' trên card để chọn loại")
                    _apply_type_in_card(page, card, removal_type, log)
                in_flight[name] = {"src": src, "dst": dst, "t0": time.time()}
                log(f"  [+] Up '{name}' — đang xử lý {len(in_flight)}/{batch_size}")
            except Exception as e:
                attempts[name] = attempts.get(name, 0) + 1
                log(f"  Lỗi up '{name}' (lần {attempts[name]}/{MAX_ATTEMPTS}): {e}")
                if attempts[name] >= MAX_ATTEMPTS:
                    # BỎ QUA đoạn này (không dừng cả mẻ) — khi ghép sẽ dùng đoạn gốc.
                    failed.append(name)
                    log(f"  [BỎ QUA] '{name}' lỗi {MAX_ATTEMPTS} lần khi up — chạy tiếp đoạn khác.")
                else:
                    pending.append((src, dst))
                break

        if should_stop():
            break

        # 2) Tải MỘT card đã xong (nếu có) rồi QUAY LẠI up bù NGAY video kế tiếp
        #    (pipeline: cái nào xong là đẩy cái mới lên, không chờ cả batch xong).
        handled = _handle_one_done_card(page, in_flight, log)
        if handled is not None:
            done += 1
            continue

        # 3) Không card nào xong -> đoạn quá lâu thì xoá + đẩy về CUỐI hàng đợi (xử
        #    lý sau cùng), rồi nghỉ ngắn trước khi poll tiếp.
        now = time.time()
        for name, info in list(in_flight.items()):
            if now - info["t0"] <= per_video_timeout:
                continue
            attempts[name] = attempts.get(name, 0) + 1
            log(f"  '{name}' quá lâu (>{int(per_video_timeout)}s) "
                f"(lần {attempts[name]}/{MAX_ATTEMPTS}) — xử lý lại.")
            c = _leaf_card_by_name(page, name)
            if c is not None:
                _delete_card(page, c, log, name=name)
            del in_flight[name]
            if attempts[name] >= MAX_ATTEMPTS:
                # BỎ QUA đoạn này (không dừng cả mẻ) — khi ghép sẽ dùng đoạn gốc.
                failed.append(name)
                log(f"  [BỎ QUA] '{name}' timeout {MAX_ATTEMPTS} lần — chạy tiếp đoạn khác.")
            else:
                pending.append((info["src"], info["dst"]))

        if in_flight or pending:
            _interruptible_sleep(2.0, should_stop)

    if failed:
        log(f"  Xong mẻ. {len(failed)} đoạn KHÔNG xử lý được (ghép sẽ dùng đoạn gốc): "
            + ", ".join(failed))
    return done


def process_videos(jobs, mode="auto", headless=False, per_video_timeout=300,
                   delay_min=1.0, delay_max=50.0, removal_type="Smart",
                   session_path=None, batch_size=10,
                   log=print, should_stop=lambda: False, **_legacy):
    """
    Xử lý các đoạn qua vmake bằng CHẾ ĐỘ BATCH: giữ `batch_size` video chạy song
    song trong cùng 1 phiên đăng nhập (login = không giới hạn). Tải về ĐÚNG theo
    TÊN file nên ghép nối không sai thứ tự. Resume: đoạn đã có file thì bỏ qua.
    """
    _use_bundled_browsers()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError(
            "Chưa cài Playwright. Chạy: pip install playwright && playwright install chromium"
        ) from e

    if delay_max < delay_min:
        delay_min, delay_max = delay_max, delay_min
    batch_size = max(1, int(batch_size or 5))

    jobs_list = list(jobs)
    pending, done = [], 0
    for src, dst in jobs_list:
        if os.path.exists(dst):
            done += 1
        else:
            pending.append((src, dst))
    if not pending:
        log("Tất cả đoạn đã có sẵn — không cần xử lý.")
        return done

    if session_path and os.path.exists(session_path):
        log(f"Dùng phiên đăng nhập: {session_path}")
    else:
        log("CHƯA đăng nhập — chạy ẩn danh (dễ bị giới hạn). Nên đăng nhập để xử lý không giới hạn.")
    log(f"Batch {batch_size} video song song. Cần xử lý {len(pending)} đoạn.")

    with sync_playwright() as p:
        browser = _launch_browser(p, headless)
        context = _new_context(browser, session_path, headless)
        page = context.new_page()
        _harden_page(context, page, log)   # chống throttle TRƯỚC khi điều hướng
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")
            _wait_dom_ready(page)
            _dismiss_overlays(page, log)
            page.wait_for_timeout(1500)
            # Phiên đăng nhập khôi phục task cũ khá TRỄ (có cái hiện sau cả chục
            # giây) -> dọn 1 lần là chưa chắc sạch: card cũ lò dò hiện sau đó sẽ
            # bị đếm nhầm thành "card mới" lúc xác nhận upload. Dọn tới khi ĐỨNG YÊN.
            for _ in range(4):
                _clear_all_task_cards(page, log)
                page.wait_for_timeout(2500)
                if not _leaf_cards(page):
                    break

            done += _run_batch(page, pending, removal_type, batch_size,
                               per_video_timeout, delay_min, delay_max,
                               log, should_stop)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return done
