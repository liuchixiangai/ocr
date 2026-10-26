"""Extract perspective-corrected, binarized crops of one region of a video.

Click the 4 corners of the region (for example the scoreboard's clock) on the
first frame, in any order. Then every Nth frame (--every) the region is warped
to an upright rectangle, binarized (Otsu) and saved as frame_<id>.png, unless
it barely changed since the last saved crop (--min-changed-pixels). The output
folder defaults to a folder named after the video, next to it.
q or Escape (or closing the window) cancels the corner selection.

Standalone tool: install requirements-tools.txt (it needs a GUI build of
OpenCV, opencv-python, not the app's opencv-python-headless, and tqdm).
"""

import argparse
import os
import sys
import uuid

import cv2
import numpy as np

WINDOW = "First Frame"


def imwrite(path, image):
    # cv2.imwrite can't open non-ASCII paths on Windows
    ok, encoded = cv2.imencode(os.path.splitext(path)[1], image)
    if not ok:
        raise OSError(f"Can't encode {path}")
    encoded.tofile(path)


def order_corners(points):
    """The 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(points, dtype=np.float32)
    sums = pts.sum(axis=1)  # x + y: smallest top-left, largest bottom-right
    diffs = pts[:, 1] - pts[:, 0]  # y - x: smallest top-right, largest bottom-left
    return np.array(
        [
            pts[np.argmin(sums)],
            pts[np.argmin(diffs)],
            pts[np.argmax(sums)],
            pts[np.argmax(diffs)],
        ],
        dtype=np.float32,
    )


def rectifying_transform(points):
    """(matrix, (width, height)) that warps the quadrilateral with corners
    points to an upright rectangle, or None if the points don't span an area."""
    corners = order_corners(points)
    top_left, top_right, bottom_right, bottom_left = corners
    width = max(
        np.linalg.norm(top_right - top_left),
        np.linalg.norm(bottom_right - bottom_left),
    )
    height = max(
        np.linalg.norm(bottom_left - top_left),
        np.linalg.norm(bottom_right - top_right),
    )
    width, height = int(round(width)), int(round(height))
    if width < 2 or height < 2 or len({tuple(p) for p in corners}) < 4:
        return None
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    return cv2.getPerspectiveTransform(corners, target), (width, height)


def binarize(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def window_closed(name):
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def select_corners(frame):
    """Let the user click 4 points on frame. Returns them, or None if cancelled."""
    points = []

    def on_mouse(event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN or len(points) >= 4:
            return
        points.append((x, y))
        canvas = frame.copy()
        for i in range(1, len(points)):
            cv2.line(canvas, points[i - 1], points[i], (0, 255, 0), 2)
        for point in points:
            cv2.circle(canvas, point, 5, (0, 255, 0), -1)
        cv2.imshow(WINDOW, canvas)

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    cv2.imshow(WINDOW, frame)
    try:
        while len(points) < 4:
            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27) or window_closed(WINDOW):
                return None
    finally:
        cv2.destroyAllWindows()
    return points


def extract_crops(cap, transform, out_folder, every, min_changed_pixels):
    """Save the crops; returns how many were written."""
    from tqdm import tqdm

    matrix, size = transform
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    last_saved = None
    written = 0
    frame_index = 0
    with tqdm(total=total_frames if total_frames > 0 else None) as progress:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_index % every == 0:
                cropped = binarize(cv2.warpPerspective(frame, matrix, size))
                # skip crops that are (almost) the same as the last saved one
                if (
                    last_saved is None
                    or cv2.countNonZero(cv2.absdiff(last_saved, cropped))
                    > min_changed_pixels
                ):
                    last_saved = cropped
                    imwrite(
                        os.path.join(out_folder, f"frame_{uuid.uuid4()}.png"), cropped
                    )
                    written += 1
            frame_index += 1
            progress.update(1)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("video", help="video file")
    parser.add_argument(
        "-o",
        "--output",
        help="where to save the crops (default: a folder named after the video)",
    )
    parser.add_argument(
        "--every",
        type=int,
        default=75,
        metavar="N",
        help="look at every Nth frame (default: %(default)s)",
    )
    parser.add_argument(
        "--min-changed-pixels",
        type=int,
        default=400,
        metavar="PIXELS",
        help="save a crop only if more pixels than this changed since the last "
        "saved one (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.every < 1:
        parser.error("--every must be at least 1")

    if not os.path.isfile(args.video):
        parser.error(f"video file does not exist: {args.video}")

    cap = cv2.VideoCapture(args.video)
    try:
        ret, frame = cap.read()
        if not ret:
            print(f"Error: unable to read the video file {args.video}")
            return 1

        points = select_corners(frame)
        if points is None:
            print("Cancelled")
            return 1
        transform = rectifying_transform(points)
        if transform is None:
            print("Error: the 4 points don't span an area")
            return 1

        out_folder = args.output or os.path.splitext(args.video)[0]
        os.makedirs(out_folder, exist_ok=True)
        print(f"Cropping and saving frames to {out_folder} ...")
        written = extract_crops(
            cap, transform, out_folder, args.every, args.min_changed_pixels
        )
    finally:
        cap.release()

    print(f"{written} frames cropped and saved successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
