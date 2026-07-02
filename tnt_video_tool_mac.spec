# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — dựng TNT Video Subtitle Remover thành .app (macOS).
PHẢI build TRÊN macOS (PyInstaller không cross-compile). Dùng build_mac.sh hoặc
GitHub Actions (.github/workflows/build-macos.yml) — không cần sở hữu máy Mac.

Gói SẴN ffmpeg/ffprobe (static) + Chromium -> máy Mac đích KHÔNG cần cài gì.
Kết quả:  dist/TNT_VideoSubtitleRemover.app

ffmpeg: đặt bản STATIC cho mac vào vendor/ffmpeg/{ffmpeg,ffprobe} (build_mac.sh tự tải).
Chromium: chạy `playwright install chromium` trước -> spec gói ~/Library/Caches/ms-playwright.

LƯU Ý Gatekeeper: app chưa notarize sẽ bị macOS chặn khi tải từ mạng. build_mac.sh /
workflow đã ad-hoc codesign; người dùng cuối chỉ cần (1 lần):
    xattr -dr com.apple.quarantine TNT_VideoSubtitleRemover.app    # gỡ cờ "tải từ internet"
hoặc chuột phải -> Open. Muốn double-click mượt hẳn cần Apple Developer ($99) để notarize.
"""
import os
import shutil

BUNDLE_FFMPEG = True
# Chromium KHÔNG nhúng qua PyInstaller: trên macOS arm64 PyInstaller codesign từng
# file, gặp nested bundle của Chromium ('Google Chrome for Testing.app' + .framework)
# -> lỗi 'bundle format unrecognized' -> build fail. Thay vào đó build_mac.sh copy
# Chromium vào .app SAU pyinstaller rồi 'codesign --deep' ký gọn cả bundle.
BUNDLE_CHROMIUM = False

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

if os.path.exists("logo.png"):
    datas += [("logo.png", ".")]

# --- ffmpeg/ffprobe (thêm vào BINARIES để giữ quyền thực thi) ---
if BUNDLE_FFMPEG:
    for tool in ("ffmpeg", "ffprobe"):
        src = os.path.join("vendor", "ffmpeg", tool)
        if not os.path.isfile(src):
            src = shutil.which(tool)
        if src and os.path.isfile(src):
            binaries += [(src, "ffmpeg")]
            print(f"[spec] nhúng {tool}: {src}")
        else:
            print(f"[spec] *** CẢNH BÁO: không thấy {tool} (đặt static vào vendor/ffmpeg/) ***")

# --- Chromium (toàn bộ ms-playwright của mac) ---
if BUNDLE_CHROMIUM:
    msp = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not msp or not os.path.isdir(msp):
        msp = os.path.expanduser("~/Library/Caches/ms-playwright")
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
    hiddenimports=hiddenimports + ["video_utils", "vmake_client", "numpy"],
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
    console=False,
    icon="logo.icns" if os.path.exists("logo.icns") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TNT_VideoSubtitleRemover",
)

app = BUNDLE(
    coll,
    name="TNT_VideoSubtitleRemover.app",
    icon="logo.icns" if os.path.exists("logo.icns") else None,
    bundle_identifier="com.tntgroup.videosubtitleremover",
    info_plist={
        "CFBundleName": "TNT Video Subtitle Remover",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
