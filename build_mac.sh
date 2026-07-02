#!/usr/bin/env bash
# Dựng TNT Video Subtitle Remover thành .app trên macOS.
# Chạy:  bash build_mac.sh   (trên máy Mac hoặc trong GitHub Actions macos runner)
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/5 Tạo venv + cài thư viện"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo "==> 2/5 Tải ffmpeg/ffprobe STATIC cho mac vào vendor/ffmpeg (đúng kiến trúc)"
mkdir -p vendor/ffmpeg
if [ ! -x vendor/ffmpeg/ffmpeg ]; then
  # PHẢI khớp kiến trúc runner: arm64 (Apple Silicon) hay x86_64 (Intel). Nếu nhúng
  # ffmpeg Intel vào app arm64 thì máy Apple Silicon phải có Rosetta mới chạy được ->
  # tải bản static theo đúng arch (martin-riedl.de có cả 2 arch, static, không phụ
  # thuộc dylib).
  case "$(uname -m)" in
    arm64)   MR=arm64 ;;
    x86_64)  MR=amd64 ;;
    *)       MR=amd64 ;;
  esac
  echo "   Kiến trúc runner: $(uname -m) -> tải ffmpeg $MR"
  curl -fL -o /tmp/ffmpeg.zip  "https://ffmpeg.martin-riedl.de/redirect/latest/macos/${MR}/release/ffmpeg.zip"
  curl -fL -o /tmp/ffprobe.zip "https://ffmpeg.martin-riedl.de/redirect/latest/macos/${MR}/release/ffprobe.zip"
  unzip -o /tmp/ffmpeg.zip  -d vendor/ffmpeg
  unzip -o /tmp/ffprobe.zip -d vendor/ffmpeg
  chmod +x vendor/ffmpeg/ffmpeg vendor/ffmpeg/ffprobe
fi
# Xác minh binary khớp kiến trúc máy đang build (bắt lỗi sớm nếu tải nhầm arch).
file vendor/ffmpeg/ffmpeg || true

echo "==> 3/5 Nạp Chromium cho Playwright"
python -m playwright install chromium

echo "==> 4/5 Build .app"
rm -rf build dist
pyinstaller tnt_video_tool_mac.spec

APP="dist/TNT_VideoSubtitleRemover.app"
echo "==> 5/5 Sửa quyền thực thi cho Chromium nhúng + ad-hoc codesign"
# datas của PyInstaller mất bit +x -> cấp lại cho mọi mach-o của chromium
find "$APP/Contents/Frameworks/ms-playwright" -type f \
  \( -name 'Chromium' -o -name 'chrome_crashpad_handler' -o -name '*.app' -prune \
     -o -path '*/MacOS/*' \) -exec chmod +x {} \; 2>/dev/null || true
chmod +x "$APP/Contents/Frameworks/ffmpeg/ffmpeg" "$APP/Contents/Frameworks/ffmpeg/ffprobe" 2>/dev/null || true
# ad-hoc sign toàn bộ (giúp nested binary chạy sau khi gỡ quarantine)
codesign --force --deep --sign - "$APP" || echo "(codesign ad-hoc lỗi — vẫn dùng được sau xattr)"

echo "==> Đóng gói zip giữ nguyên symlink/quyền"
( cd dist && ditto -c -k --sequesterRsrc --keepParent \
    TNT_VideoSubtitleRemover.app TNT_VideoSubtitleRemover-mac.zip )

echo "XONG: dist/TNT_VideoSubtitleRemover.app  (+ dist/TNT_VideoSubtitleRemover-mac.zip để gửi)"
