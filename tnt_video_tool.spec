# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller spec — dựng TNT Video Subtitle Remover thành .exe (Windows, one-dir).
Gói SẴN ffmpeg/ffprobe + Chromium -> máy đích KHÔNG cần cài gì thêm.

Build:
    pip install -r requirements.txt pyinstaller
    playwright install chromium          # nạp Chromium vào ms-playwright (để nhúng)
    pyinstaller tnt_video_tool.spec

Kết quả:  dist/TNT_VideoSubtitleRemover/TNT_VideoSubtitleRemover.exe

Hai công tắc bên dưới (BUNDLE_FFMPEG / BUNDLE_CHROMIUM):
- True  = nhúng sẵn vào exe (máy trắng vẫn chạy; exe nặng hơn — Chromium ~300MB).
- False = không nhúng (exe nhẹ; máy đích phải tự có ffmpeg trong PATH / Google Chrome).

Nguồn nhị phân lúc build:
- ffmpeg/ffprobe: lấy từ PATH (shutil.which), hoặc đặt sẵn vào vendor/ffmpeg/ trong project.
- Chromium: lấy từ thư mục ms-playwright (PLAYWRIGHT_BROWSERS_PATH hoặc %LOCALAPPDATA%\ms-playwright).

Phiên ĐĂNG NHẬP: exe VẪN có nút đăng nhập. Session lưu theo TỪNG MÁY tại
%USERPROFILE%\.tnt_video_tool\vmake_session.json — KHÔNG nằm trong exe, nên bản phát
hành không chứa session của người build; mỗi người dùng tự đăng nhập & lưu session riêng.
"""
import os
import shutil
from PyInstaller.utils.hooks import collect_all

BUNDLE_FFMPEG = True       # nhúng ffmpeg + ffprobe
BUNDLE_CHROMIUM = True     # nhúng trình duyệt Chromium của Playwright

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

if os.path.exists("logo.png"):
    datas += [("logo.png", ".")]

# --- Nhúng ffmpeg/ffprobe vào thư mục con ffmpeg/ (find_binary sẽ tìm ở đây) ---
if BUNDLE_FFMPEG:
    for tool in ("ffmpeg", "ffprobe"):
        src = os.path.join("vendor", "ffmpeg", tool + ".exe")   # ưu tiên bản trong project
        if not os.path.isfile(src):
            src = shutil.which(tool)
        if src and os.path.isfile(src):
            datas += [(src, "ffmpeg")]
            print(f"[spec] nhúng {tool}: {src}")
        else:
            print(f"[spec] *** CẢNH BÁO: không thấy {tool} để nhúng (đặt vào vendor/ffmpeg/) ***")

# --- Nhúng Chromium (toàn bộ ms-playwright) -> runtime trỏ PLAYWRIGHT_BROWSERS_PATH vào đây ---
if BUNDLE_CHROMIUM:
    msp = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not msp or not os.path.isdir(msp):
        msp = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                           "ms-playwright")
    if os.path.isdir(msp):
        nfile = 0
        for root, _dirs, files in os.walk(msp):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(os.path.dirname(full), msp)
                dest = "ms-playwright" if rel == "." else os.path.join("ms-playwright", rel)
                datas += [(full, dest)]
                nfile += 1
        print(f"[spec] nhúng Chromium từ {msp} ({nfile} file)")
    else:
        print("[spec] *** CẢNH BÁO: không thấy ms-playwright — chạy 'playwright install chromium' trước ***")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["video_utils", "vmake_client", "numpy",
                                   "tnt_license", "cryptography"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TNT_VideoSubtitleRemover",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # app GUI -> không bật cửa sổ console
    icon="logo.ico" if os.path.exists("logo.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TNT_VideoSubtitleRemover",
)
