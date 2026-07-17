import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import replicate
import requests


# Model ProPainter pinned version
PROPAINTER_MODEL = (
    "jd7h/propainter:"
    "e5ea7ae04e97c96a0e14c70d8e4cb899abdf326a377c01f1c10966ccd6c6bae4"
)

# Tọa độ mask theo video gốc 576x1024 của bạn
# Format: x1, y1, x2, y2
FIXED_RECTS_BASE = [
    (55, 135, 530, 285),
    (35, 245, 545, 380),
    (85, 340, 525, 470),
]

BASE_W = 576
BASE_H = 1024


def get_video_info(video_path: str):
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    cap.release()

    if fps <= 0:
        fps = 30.0

    duration = frames / fps if fps > 0 else 0

    return width, height, fps, frames, duration


def cut_preview_opencv(input_path: str, preview_path: str, seconds: float = 5.0):
    """
    Cắt N giây đầu video bằng OpenCV.
    Output không có audio để file nhẹ và dễ upload Replicate.
    """
    cap = cv2.VideoCapture(input_path)

    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video input: {input_path}")

    width, height, fps, frames, duration = get_video_info(input_path)

    max_frames = min(int(seconds * fps), frames)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(preview_path, fourcc, fps, (width, height))

    if not writer.isOpened():
        raise RuntimeError(f"Không tạo được preview video: {preview_path}")

    count = 0

    while count < max_frames:
        ok, frame = cap.read()

        if not ok:
            break

        writer.write(frame)
        count += 1

    cap.release()
    writer.release()

    if count == 0:
        raise RuntimeError("Không cắt được frame nào từ video.")

    return preview_path


def scale_rects(rects, width, height, base_w=BASE_W, base_h=BASE_H):
    sx = width / base_w
    sy = height / base_h

    scaled = []

    for x1, y1, x2, y2 in rects:
        nx1 = max(0, min(width - 1, int(round(x1 * sx))))
        ny1 = max(0, min(height - 1, int(round(y1 * sy))))
        nx2 = max(0, min(width, int(round(x2 * sx))))
        ny2 = max(0, min(height, int(round(y2 * sy))))

        scaled.append((nx1, ny1, nx2, ny2))

    return scaled


def make_fixed_mask(video_path: str, mask_path: str, dilate_px: int = 25, blur_sigma: float = 3.0):
    width, height, fps, frames, duration = get_video_info(video_path)

    mask = np.zeros((height, width), dtype=np.uint8)

    scaled_rects = scale_rects(FIXED_RECTS_BASE, width, height)

    for x1, y1, x2, y2 in scaled_rects:
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)

    # Nới mask để ăn cả viền đen, bóng, glow, phông nền sau chữ
    if dilate_px > 0:
        k = dilate_px * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.dilate(mask, kernel, iterations=1)

    # Làm mềm biên mask
    if blur_sigma > 0:
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=blur_sigma, sigmaY=blur_sigma)

    cv2.imwrite(mask_path, mask)

    return mask_path, scaled_rects


def make_mask_preview(video_path: str, mask_path: str, preview_jpg_path: str, rects):
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video preview: {video_path}")

    ok, frame = cap.read()
    cap.release()

    if not ok:
        raise RuntimeError("Không đọc được frame đầu để tạo preview mask.")

    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

    if mask is None:
        raise RuntimeError(f"Không đọc được mask: {mask_path}")

    red = np.zeros_like(frame)
    red[:, :, 2] = 255

    alpha = (mask.astype(np.float32) / 255.0) * 0.45

    overlay = (
        frame.astype(np.float32) * (1 - alpha[:, :, None])
        + red.astype(np.float32) * alpha[:, :, None]
    ).astype(np.uint8)

    for x1, y1, x2, y2 in rects:
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 255), 2)

    cv2.imwrite(preview_jpg_path, overlay)

    return preview_jpg_path


def save_replicate_output(output, output_path: str):
    """
    Replicate output có thể là:
    - FileOutput có .read()
    - URL string
    - list chứa FileOutput hoặc URL
    """
    if isinstance(output, list):
        if len(output) == 0:
            raise RuntimeError("Replicate trả về output rỗng.")
        output = output[0]

    if hasattr(output, "read"):
        data = output.read()

        with open(output_path, "wb") as f:
            f.write(data)

        return output_path

    if isinstance(output, str):
        r = requests.get(output, timeout=600)
        r.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(r.content)

        return output_path

    raise RuntimeError(f"Không biết cách lưu output kiểu: {type(output)}")


def run_propainter(token: str, video_path: str, mask_path: str, output_path: str):
    client = replicate.Client(api_token=token)

    with open(video_path, "rb") as video_file, open(mask_path, "rb") as mask_file:
        output = client.run(
            PROPAINTER_MODEL,
            input={
                "video": video_file,
                "mask": mask_file,

                # Vì mask local đã dilate rồi nên để thấp.
                # Nếu còn sót viền/glow, tăng local --dilate trước.
                "mask_dilation": 8,

                # Giữ nguyên size preview.
                # Nếu muốn rẻ/nhanh hơn có thể đổi 0.75 hoặc 0.5.
                "resize_ratio": 1.0,

                "neighbor_length": 10,
                "ref_stride": 10,
                "subvideo_length": 80,
                "raft_iter": 20,
                "fp16": True,
                "return_input_video": False,
            },
        )

    return save_replicate_output(output, output_path)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        required=True,
        help="Video input, ví dụ: 2.mp4",
    )

    parser.add_argument(
        "--token",
        required=True,
        help="Replicate API token, ví dụ: r8_xxx",
    )

    parser.add_argument(
        "--output",
        default="preview_clean.mp4",
        help="Video output sau khi chạy ProPainter preview",
    )

    parser.add_argument(
        "--seconds",
        type=float,
        default=5.0,
        help="Số giây đầu cần cắt để test preview. Mặc định 5s.",
    )

    parser.add_argument(
        "--dilate",
        type=int,
        default=25,
        help="Nới mask local để ăn viền/shadow/glow. Nên 20-35.",
    )

    parser.add_argument(
        "--blur",
        type=float,
        default=3.0,
        help="Làm mềm biên mask.",
    )

    args = parser.parse_args()

    input_path = str(Path(args.input).resolve())
    output_path = str(Path(args.output).resolve())

    if not os.path.exists(input_path):
        print(f"Không tìm thấy input video: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(input_path).stem

    preview_video_path = str(out_dir / f"{stem}_preview_{int(args.seconds)}s.mp4")
    mask_path = str(out_dir / f"{stem}_preview_mask.png")
    mask_preview_path = str(out_dir / f"{stem}_preview_mask_check.jpg")

    print("[1/4] Đọc video input...")

    width, height, fps, frames, duration = get_video_info(input_path)

    print(f"Input: {input_path}")
    print(f"Size: {width}x{height}")
    print(f"FPS: {fps}")
    print(f"Frames: {frames}")
    print(f"Duration: {duration:.2f}s")

    print(f"[2/4] Cắt {args.seconds}s đầu để test preview...")

    cut_preview_opencv(
        input_path=input_path,
        preview_path=preview_video_path,
        seconds=args.seconds,
    )

    p_w, p_h, p_fps, p_frames, p_duration = get_video_info(preview_video_path)

    print(f"Preview video: {preview_video_path}")
    print(f"Preview size: {p_w}x{p_h}, fps={p_fps}, frames={p_frames}, duration={p_duration:.2f}s")

    print("[3/4] Tạo fixed mask cho preview...")

    mask_path, rects = make_fixed_mask(
        video_path=preview_video_path,
        mask_path=mask_path,
        dilate_px=args.dilate,
        blur_sigma=args.blur,
    )

    make_mask_preview(
        video_path=preview_video_path,
        mask_path=mask_path,
        preview_jpg_path=mask_preview_path,
        rects=rects,
    )

    print(f"Mask: {mask_path}")
    print(f"Mask preview check: {mask_preview_path}")
    print(f"Rects: {rects}")

    print("[4/4] Gọi Replicate ProPainter chạy preview...")

    result = run_propainter(
        token=args.token,
        video_path=preview_video_path,
        mask_path=mask_path,
        output_path=output_path,
    )

    print("DONE")
    print(f"Preview input cut: {preview_video_path}")
    print(f"Mask: {mask_path}")
    print(f"Mask check: {mask_preview_path}")
    print(f"Output clean preview: {result}")


if __name__ == "__main__":
    main()