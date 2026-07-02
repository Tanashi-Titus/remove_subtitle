"""
video_utils.py
Các hàm tách (split) và ghép (merge) video bằng ffmpeg/ffprobe.
"""

import os
import re
import shutil
import subprocess
import sys

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts"}

# Cờ ẩn cửa sổ console đen của ffmpeg/ffprobe trên Windows (rõ nhất khi chạy bản .exe
# GUI: mỗi lần gọi ffmpeg sẽ KHÔNG còn nháy cửa sổ đen). Non-Windows = 0 (không ảnh hưởng).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------------------------------------------------------
# Tiện ích chung
# ----------------------------------------------------------------------------
def find_binary(name):
    """
    Tìm ffmpeg/ffprobe. Ưu tiên bản ĐÓNG GÓI trong .exe (thư mục con `ffmpeg/` cạnh
    exe hoặc trong `_internal/`), nếu không có mới tới PATH của máy. Nhờ vậy máy đích
    CHƯA cài ffmpeg vẫn chạy được bản .exe.
    """
    bases = []
    mp = getattr(sys, "_MEIPASS", None)        # PyInstaller: thư mục giải nén tài nguyên
    if mp:
        bases.append(mp)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        bases += [exe_dir, os.path.join(exe_dir, "_internal")]
    fname = name + (".exe" if os.name == "nt" else "")
    for b in bases:
        for cand in (os.path.join(b, "ffmpeg", fname), os.path.join(b, fname)):
            if os.path.isfile(cand):
                if os.name != "nt":          # mac/linux: đảm bảo có quyền thực thi
                    try:
                        os.chmod(cand, os.stat(cand).st_mode | 0o111)
                    except OSError:
                        pass
                return cand
    return shutil.which(name)


def sanitize(name):
    """Đổi tên file/folder thành dạng an toàn (bỏ ký tự đặc biệt)."""
    s = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    return s or "video"


def run_cmd(cmd):
    """Chạy 1 lệnh, ném lỗi kèm log nếu thất bại."""
    out = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        msg = (out.stderr or out.stdout or "").strip()
        raise RuntimeError(msg[-2000:])
    return out


def collect_videos(input_path, recursive=False):
    """Nhận 1 file video hoặc 1 folder, trả về danh sách đường dẫn video đã sắp xếp."""
    input_path = os.path.abspath(input_path)
    if os.path.isfile(input_path):
        if os.path.splitext(input_path)[1].lower() in VIDEO_EXTS:
            return [input_path]
        raise RuntimeError(f"File không phải video được hỗ trợ: {input_path}")

    if os.path.isdir(input_path):
        found = []
        if recursive:
            for root, _dirs, files in os.walk(input_path):
                for f in files:
                    if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                        found.append(os.path.join(root, f))
        else:
            for f in os.listdir(input_path):
                full = os.path.join(input_path, f)
                if os.path.isfile(full) and os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                    found.append(full)
        return sorted(found)

    raise RuntimeError(f"Đường dẫn không tồn tại: {input_path}")


# ----------------------------------------------------------------------------
# Đọc thời lượng & tính các đoạn cắt
# ----------------------------------------------------------------------------
def ffprobe_duration(path, ffprobe="ffprobe"):
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    out = run_cmd(cmd)
    s = out.stdout.strip()
    try:
        return float(s)
    except ValueError:
        raise RuntimeError(f"Không đọc được thời lượng của: {path}")


def compute_segments(duration, seg_seconds=4.0, min_tail=0.15):
    """
    Chia [0, duration] thành các đoạn dài seg_seconds, đoạn cuối lấy phần còn lại.
    Ví dụ duration=11.8, seg=4 -> [(0,4),(4,4),(8,3.8)].
    Đoạn đuôi quá ngắn (< min_tail) sẽ gộp vào đoạn liền trước.
    Trả về list các tuple (start, length).
    """
    segments = []
    if duration <= 0:
        return segments
    t = 0.0
    while t < duration - 1e-4:
        length = min(seg_seconds, duration - t)
        segments.append((round(t, 4), round(length, 4)))
        t += seg_seconds

    if len(segments) >= 2 and segments[-1][1] < min_tail:
        ps, pl = segments[-2]
        _ls, ll = segments[-1]
        segments[-2] = (ps, round(pl + ll, 4))
        segments.pop()
    return segments


# ----------------------------------------------------------------------------
# Tách video
# ----------------------------------------------------------------------------
# Cắt DƯ phần đuôi mỗi đoạn (overlap). THỰC NGHIỆM: vmake VỨT vài frame ở ĐẦU mỗi
# đoạn (không phải xén đuôi như từng nghĩ) -> phần dư đuôi này khiến đoạn trước phủ
# lên vùng đầu (bị mất) của đoạn sau, nên lúc GHÉP `merge_segments` căn nội dung và
# nối liền mạch được, không trùng không hụt. Xem `_align_starts`.
SEG_OVERLAP = 0.5      # giây cắt dư thêm vào đuôi (để đoạn trước phủ phần đầu đoạn sau)
PREVIEW_MAX = 4.95     # tổng độ dài 1 đoạn phải ≤ 5s (giới hạn preview free của vmake)


def split_video(src, segments_root, seg_seconds=4.0,
                ffmpeg="ffmpeg", ffprobe="ffprobe",
                crf=18, preset="veryfast", log=print, should_stop=lambda: False):
    """
    Cắt 1 video thành các đoạn seg_seconds giây, lưu vào segments_root/<safe>/.
    Mỗi đoạn được cắt DƯ thêm `SEG_OVERLAP` giây ở đuôi (nhưng ≤ 5s để vmake free
    vẫn tải đủ) để bù phần vmake xén; độ dài THẬT (`dur`) vẫn là seg_seconds và sẽ
    được cắt lại đúng khi ghép -> tổng video ghép = đúng video gốc.
    """
    name = os.path.splitext(os.path.basename(src))[0]
    safe = sanitize(name)
    vdir = os.path.join(segments_root, safe)
    os.makedirs(vdir, exist_ok=True)

    duration = ffprobe_duration(src, ffprobe)
    src_fps = _detect_fps(src, ffprobe)        # fps GỐC, lưu vào manifest để ghép dùng
    segs = compute_segments(duration, seg_seconds)
    log(f"  Thời lượng {duration:.2f}s, {src_fps}fps -> {len(segs)} đoạn")

    enc = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    n = len(segs)
    seg_records = []
    for i, (start, length) in enumerate(segs):
        if should_stop():
            break
        avail = duration - start                         # nội dung thật còn lại
        # Đoạn cắt mong muốn = độ dài thật + overlap (≤ 5s preview). Phần overlap lấy
        # từ nội dung thật nếu còn (đoạn giữa); nếu hết video (đoạn cuối) thì ĐỆM
        # bằng freeze frame cuối để vmake xén vào đó.
        target_cut = min(length + SEG_OVERLAP, PREVIEW_MAX)
        content_dur = min(target_cut, avail)             # nội dung thật cắt được
        pad = max(0.0, target_cut - content_dur)         # đệm freeze (chủ yếu đoạn cuối)
        out_path = os.path.join(vdir, f"{safe}_{i:03d}.mp4")
        if not os.path.exists(out_path):
            if pad < 0.02:
                run_cmd([ffmpeg, "-y", "-ss", f"{start:.4f}", "-i", src,
                         "-t", f"{content_dur:.4f}", *enc, out_path])
            else:
                raw = out_path + ".raw.mp4"
                run_cmd([ffmpeg, "-y", "-ss", f"{start:.4f}", "-i", src,
                         "-t", f"{content_dur:.4f}", *enc, raw])
                run_cmd([ffmpeg, "-y", "-i", raw,
                         "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.4f},format=yuv420p",
                         "-af", f"apad=pad_dur={pad:.4f}",
                         *enc, out_path])
                try:
                    os.remove(raw)
                except OSError:
                    pass
            log(f"  Cắt đoạn {i + 1}/{n}: {start:.2f}s (dài thật {length:.2f}s, "
                f"đoạn {target_cut:.2f}s{' +đệm đuôi' if pad >= 0.02 else ''})")
        else:
            log(f"  Đoạn {i + 1}/{n} đã tồn tại, bỏ qua")
        seg_records.append({
            "index": i,
            "file": out_path,
            "start": start,
            "dur": length,        # độ dài THẬT (để ghép cắt lại cho khít)
            "cut_dur": target_cut,
        })

    return {
        "source": os.path.abspath(src),
        "name": name,
        "safe": safe,
        "duration": duration,
        "fps": src_fps,
        "segments": seg_records,
    }


# ----------------------------------------------------------------------------
# Ghép video
# ----------------------------------------------------------------------------
def _detect_fps(path, ffprobe="ffprobe"):
    """Đọc fps của video (mặc định 30 nếu không đọc được)."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        s = (out.stdout or "").strip()
        if "/" in s:
            a, b = s.split("/")
            fps = float(a) / float(b) if float(b) else 30.0
        else:
            fps = float(s)
        if fps <= 0 or fps > 120:
            fps = 30.0
        return round(fps, 3)
    except Exception:
        return 30.0


def _detect_size(path, ffprobe="ffprobe"):
    """Đọc (width, height) của video, hoặc (None, None)."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        s = (out.stdout or "").strip()
        if "x" in s:
            w, h = s.split("x")[:2]
            return int(w), int(h)
    except Exception:
        pass
    return None, None


def _has_audio(path, ffprobe="ffprobe"):
    """
    Đoạn này có luồng AUDIO không? vmake đôi khi trả bản preview KHÔNG có tiếng
    (nhất là đoạn đuôi/đệm freeze) -> concat filter tham chiếu [i:a] sẽ báo
    'matches no streams' và ghép hỏng. Dò trước để đoạn nào thiếu tiếng thì chèn
    im lặng khi ghép. Lỗi dò -> giả định CÓ (giữ hành vi cũ).
    """
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        return bool((out.stdout or "").strip())
    except Exception:
        return True


# ----------------------------------------------------------------------------
# CĂN FRAME THEO NỘI DUNG (sửa lệch do vmake bỏ frame ở ĐẦU mỗi đoạn)
# ----------------------------------------------------------------------------
# Thực nghiệm: vmake KHÔNG xén đuôi mà VỨT vài frame ở ĐẦU mỗi đoạn (≈3 frame @30fps,
# 4–6 frame @60fps, biến thiên theo video/đoạn) rồi giữ nguyên phần còn lại. Vì số
# frame bỏ KHÔNG cố định nên không thể cắt theo 1 hằng số. Cách chắc ăn: đối chiếu
# từng đoạn ĐÃ XỬ LÝ với VIDEO GỐC để biết đoạn đó thực sự bắt đầu ở frame gốc nào,
# rồi cắt liền mạch -> không lệch tích lũy, bất kể vmake bỏ bao nhiêu frame.
_GW, _GH = 64, 36   # kích thước thu nhỏ khi đối chiếu (gray)


def _decode_gray(path, ffmpeg, fps, ss=None, t=None, nframes=None):
    """Giải mã video thành mảng numpy [n, _GW*_GH] (gray, đã chuẩn hoá để đối chiếu
    bằng tương quan). Trả về None nếu không có numpy hoặc giải mã lỗi."""
    try:
        import numpy as np
    except Exception:
        return None
    cmd = [ffmpeg, "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{max(0.0, ss):.4f}"]
    cmd += ["-i", path]
    if t is not None:
        cmd += ["-t", f"{t:.4f}"]
    if nframes is not None:
        cmd += ["-frames:v", str(int(nframes))]
    cmd += ["-vf", f"fps={fps},scale={_GW}:{_GH},format=gray", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=120,
                             creationflags=_NO_WINDOW).stdout
    except Exception:
        return None
    sz = _GW * _GH
    n = len(raw) // sz
    if n == 0:
        return None
    a = np.frombuffer(raw[:n * sz], dtype=np.uint8).reshape(n, sz).astype(np.float32)
    a -= a.mean(axis=1, keepdims=True)
    nrm = np.linalg.norm(a, axis=1, keepdims=True)
    nrm[nrm == 0] = 1.0
    return a / nrm


def _align_starts(seg_files, starts, durations, fps, duration, source_path, ffmpeg, log):
    """
    Với mỗi đoạn đã xử lý, tìm CHỈ SỐ FRAME GỐC mà frame-0 của đoạn khớp nhất (đối
    chiếu nội dung). Trả về:
      front_pad : số frame freeze cần chèn ở ĐẦU video (vì frame đầu tiên của cả
                  video đã bị vmake vứt, không khôi phục được) — giữ tổng dài = gốc.
      lengths   : số frame mỗi đoạn ĐÓNG GÓP (đã căn để các mối nối liền mạch).
    Trả về None nếu không đủ điều kiện (thiếu numpy / source / đối chiếu kém) -> caller
    fallback về cách cũ.
    """
    try:
        import numpy as np
    except Exception:
        log("  (Không có numpy — bỏ qua căn frame, dùng cách cắt theo độ dài.)")
        return None
    if not source_path or not os.path.exists(source_path):
        log("  (Không thấy video gốc — bỏ qua căn frame.)")
        return None
    if not starts or any(s is None for s in starts):
        return None

    N = int(round(duration * fps))            # tổng frame của video gốc
    K = 6                                      # số frame liên tiếp dùng để đối chiếu
    SEARCH = max(0.4, 2.0 / fps + 0.4)         # cửa sổ dò quanh vị trí kỳ vọng (giây)
    anchors = []                               # a_i = frame gốc ứng với frame-0 đoạn i
    for i, sf in enumerate(seg_files):
        start = float(starts[i])
        expected = int(round(start * fps))
        win_ss = max(0.0, start - SEARCH)
        base = int(round(win_ss * fps))
        O = _decode_gray(source_path, ffmpeg, fps, ss=win_ss, t=2 * SEARCH + K / fps + 0.2)
        P = _decode_gray(sf, ffmpeg, fps, nframes=K)
        if O is None or P is None or len(O) < K or len(P) < 1:
            log("  (Đối chiếu frame thất bại ở 1 đoạn — dùng cách cũ.)")
            return None
        kk = min(K, len(P), len(O))
        best_c, best_score = 0, -1e9
        for c in range(0, len(O) - kk + 1):
            score = float(np.sum(O[c:c + kk] * P[:kk]))   # tổng tương quan kk frame
            # ưu tiên vị trí gần kỳ vọng khi điểm xấp xỉ nhau (cảnh tĩnh dễ trùng)
            score -= 1e-4 * abs((base + c) - expected)
            if score > best_score:
                best_score, best_c = score, c
        anchors.append((expected, base + best_c))   # (kỳ vọng, frame gốc khớp)

    # vmake bỏ ~cùng số frame đầu ở mọi đoạn -> drop_i = (khớp - kỳ vọng) phải xấp xỉ
    # nhau. Lấy MEDIAN drop làm chuẩn rồi SỬA các đoạn lệch hẳn (vd intro fade khiến
    # đối chiếu sai) về median -> bền với 1–2 đoạn khó.
    drops = sorted(a - e for e, a in anchors)
    med = drops[len(drops) // 2]
    if med < 0:
        med = 0
    fixed = []
    for e, a in anchors:
        d = a - e
        if abs(d - med) > 2 or d < 0:          # lệch median > 2 frame -> coi là sai
            d = med
        fixed.append(e + d)
    # bảo đảm tăng dần (đề phòng sai số) — frame gốc đoạn sau luôn > đoạn trước.
    anchors = []
    for k, a in enumerate(fixed):
        if k and a <= anchors[-1]:
            a = anchors[-1] + 1
        anchors.append(a)

    # tính độ dài đóng góp = khoảng cách tới đoạn kế.
    front_pad = max(0, anchors[0])
    lengths = []
    for i in range(len(anchors)):
        if i < len(anchors) - 1:
            L = anchors[i + 1] - anchors[i]
        else:
            # đoạn cuối: lấp cho đủ tổng N frame.
            L = (N - front_pad) - sum(lengths)
        if L <= 0:
            log("  (Căn frame cho khoảng âm — dùng cách cũ.)")
            return None
        # chặn không vượt số frame thực có của đoạn đã xử lý
        avail = int(round(float(durations[i] if durations and durations[i] else 0) * fps)) + \
            int(round(SEG_OVERLAP * fps)) + K + 4
        lengths.append(min(L, avail))
    log(f"  Căn frame OK: bỏ {front_pad} frame đầu (vmake vứt), {len(lengths)} đoạn khớp gốc.")
    # med = số frame ĐẦU vmake bỏ ở MỖI đoạn -> dùng để căn tiếng gốc khi phải ghép
    # lại tiếng cho đoạn vmake làm rớt audio (video vmake đã bỏ `med` frame đầu nên
    # tiếng gốc cũng phải bỏ tương ứng cho khớp môi/tiếng).
    return front_pad, lengths, med


def merge_segments(seg_files, out_path, ffmpeg="ffmpeg", ffprobe="ffprobe",
                   crf=18, preset="veryfast", log=print, durations=None,
                   starts=None, source_path=None, target_fps=None,
                   audio_files=None):
    """
    Ghép các đoạn thành 1 video bằng MỘT lượt encode (concat filter) — KHỚP TUYỆT
    ĐỐI với video gốc, KHÔNG đổi kích thước/hướng video:
    - CĂN FRAME THEO NỘI DUNG: nếu có `starts` + video gốc (`source_path`), mỗi đoạn
      được đối chiếu với gốc để biết nó thực sự bắt đầu ở frame gốc nào (vmake bỏ vài
      frame đầu, biến thiên theo đoạn/fps) rồi cắt liền mạch -> KHỚP tới frame, không
      lệch tích lũy. Thiếu numpy/source -> fallback cắt theo độ dài thật `durations[i]`.
    - Chuẩn về fps GỐC `target_fps` (không để vmake đẩy 60fps), CFR.
    - GIỮ NGUYÊN kích thước/hướng của đoạn (KHÔNG scale/pad -> không lật, không viền đen).
    - Ép ĐÚNG SỐ FRAME = tổng độ dài gốc × fps -> dài đúng bằng video gốc tới ms.
    target_fps: fps GỐC (từ manifest). source_path: video gốc (để căn frame + dự phòng
    đọc fps). durations: độ dài thật mỗi đoạn. starts: mốc bắt đầu mỗi đoạn (để căn).
    audio_files: nguồn TIẾNG thay thế (đoạn GỐC bạn cắt) cho đoạn nào bị vmake làm
    RỚT audio — xoá phụ đề không đổi tiếng nên ghép lại tiếng gốc là khớp; thiếu
    nguồn thì mới chèn im lặng.
    """
    missing = [p for p in seg_files if not os.path.exists(p)]
    if missing:
        raise RuntimeError("Thiếu các đoạn đã xử lý:\n" + "\n".join(missing))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if target_fps:
        fps = float(target_fps)
    else:
        ref = source_path if (source_path and os.path.exists(source_path)) else seg_files[0]
        fps = _detect_fps(ref, ffprobe)
    total_dur = None
    if durations:
        total_dur = sum(float(d) for d in durations if d)

    # CĂN FRAME: thử dò chính xác frame gốc mỗi đoạn bắt đầu (sửa lệch do vmake bỏ
    # frame đầu). Nếu được -> dùng số frame căn (`lengths` + `front_pad`); nếu không
    # -> fallback cắt theo độ dài thật `durations` như cũ.
    align = None
    duration = float(total_dur) if total_dur else None
    if duration:
        try:
            align = _align_starts(seg_files, starts, durations, fps, duration,
                                  source_path, ffmpeg, log)
        except Exception as e:
            log(f"  (Căn frame lỗi: {e} — dùng cách cũ.)")
            align = None

    # Filtergraph: cắt + chuẩn fps từng đoạn rồi concat. KHÔNG scale/pad -> giữ
    # nguyên kích thước & hướng gốc (concat yêu cầu các đoạn cùng kích thước — vmake
    # giữ nguyên kích thước nên OK).
    parts = []
    concat_in = ""
    k = len(seg_files)

    # Đoạn nào KHÔNG có tiếng (vmake đôi khi bỏ audio khi export):
    #  - Ưu tiên GHÉP LẠI tiếng từ đoạn GỐC bạn cắt (`audio_files[i]`) — xoá phụ đề
    #    không đổi tiếng nên nội dung y hệt, chỉ căn bù `head_drop` frame vmake bỏ.
    #  - Không có nguồn thay thế -> chèn im lặng để concat không báo
    #    '[i:a] matches no streams'.
    head_drop = align[2] if align is not None else 0     # số frame đầu vmake bỏ mỗi đoạn
    have_audio = [_has_audio(p, ffprobe) for p in seg_files]
    graft = {}          # i -> chỉ số input phụ (nguồn tiếng gốc)
    extra_inputs = []   # các file audio gốc thêm vào sau seg_files
    for i, ok in enumerate(have_audio):
        if ok:
            continue
        af = audio_files[i] if (audio_files and i < len(audio_files)) else None
        if af and af != seg_files[i] and os.path.exists(af) and _has_audio(af, ffprobe):
            graft[i] = k + len(extra_inputs)
            extra_inputs.append(af)
    if graft:
        log(f"  (Đoạn vmake rớt tiếng: {sorted(graft)} — GHÉP LẠI tiếng từ đoạn gốc.)")
    silent = [i for i, ok in enumerate(have_audio) if not ok and i not in graft]
    if silent:
        log(f"  (Đoạn thiếu tiếng & không có nguồn gốc: {silent} — chèn im lặng.)")

    def _silence(i, adur):
        """Chuỗi audio IM LẶNG dài `adur` giây cho đoạn thiếu tiếng."""
        return (f"anullsrc=r=44100:cl=stereo,atrim=0:{max(0.01, adur):.4f},"
                f"aformat=sample_rates=44100:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS[a{i}]")

    if align is not None:
        front_pad, lengths = align[0], align[1]
        for i, p in enumerate(seg_files):
            L = int(lengths[i])
            pad = front_pad if (i == 0 and front_pad > 0) else 0
            # video: chuẩn fps -> lấy đúng L frame đầu (frame-0 đã khớp gốc).
            vchain = f"fps={fps},trim=start_frame=0:end_frame={L}"
            if pad:                              # chèn freeze ở đầu video (frame gốc đã mất)
                vchain += f",tpad=start_mode=clone:start_duration={pad / fps:.4f}"
            parts.append(f"[{i}:v]{vchain},format=yuv420p,setsar=1,"
                         f"setpts=PTS-STARTPTS[v{i}]")
            # audio: cắt đúng thời lượng tương ứng; đoạn đầu đệm im lặng phần freeze.
            if have_audio[i]:
                achain = f"[{i}:a]atrim=0:{L / fps:.4f}"
                if pad:
                    achain += f",adelay={int(round(pad / fps * 1000))}:all=1"
                parts.append(achain + ",aformat=sample_rates=44100:"
                             f"channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
            elif i in graft:
                # tiếng GỐC: bỏ `head_drop/fps` giây đầu cho khớp video vmake (đã bỏ
                # bấy nhiêu frame), lấy đúng L/fps giây.
                s = head_drop / fps
                achain = f"[{graft[i]}:a]atrim=start={s:.4f}:end={s + L / fps:.4f}"
                if pad:
                    achain += f",adelay={int(round(pad / fps * 1000))}:all=1"
                parts.append(achain + ",aformat=sample_rates=44100:"
                             f"channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
            else:
                parts.append(_silence(i, (L + pad) / fps))
            concat_in += f"[v{i}][a{i}]"
    else:
        for i, p in enumerate(seg_files):
            d = durations[i] if (durations and i < len(durations) and durations[i]) else None
            vt = f"trim=0:{float(d):.4f}," if d else ""
            parts.append(f"[{i}:v]{vt}fps={fps},format=yuv420p,setsar=1,"
                         f"setpts=PTS-STARTPTS[v{i}]")
            if have_audio[i]:
                at = f"atrim=0:{float(d):.4f}," if d else ""
                parts.append(f"[{i}:a]{at}aformat=sample_rates=44100:channel_layouts=stereo,"
                             f"asetpts=PTS-STARTPTS[a{i}]")
            elif i in graft:
                at = f"atrim=0:{float(d):.4f}," if d else ""
                parts.append(f"[{graft[i]}:a]{at}aformat=sample_rates=44100:"
                             f"channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
            else:
                if d:
                    adur = float(d)
                else:
                    try:
                        adur = ffprobe_duration(p, ffprobe)
                    except Exception:
                        adur = 4.0
                parts.append(_silence(i, adur))
            concat_in += f"[v{i}][a{i}]"
    parts.append(f"{concat_in}concat=n={k}:v=1:a=1[outv][outa]")
    graph = ";".join(parts)

    script = out_path + ".filter.txt"
    with open(script, "w", encoding="utf-8") as f:
        f.write(graph)
    inputs = []
    for p in seg_files:
        inputs += ["-i", p]
    for p in extra_inputs:          # nguồn tiếng gốc cho đoạn vmake rớt audio
        inputs += ["-i", p]
    cmd = [ffmpeg, "-y", *inputs, "-filter_complex_script", script,
           "-map", "[outv]", "-map", "[outa]"]
    nframes = None
    if total_dur:
        nframes = int(round(total_dur * fps))
        cmd += ["-frames:v", str(nframes), "-t", f"{total_dur:.4f}", "-shortest"]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-video_track_timescale", "90000", "-movflags", "+faststart", out_path,
    ]
    try:
        log(f"  Ghép {k} đoạn (fps gốc={fps}, giữ nguyên kích thước, "
            f"{nframes or 0} frame ≈ {total_dur or 0:.2f}s)…")
        run_cmd(cmd)
    finally:
        try:
            os.remove(script)
        except OSError:
            pass
    return out_path
