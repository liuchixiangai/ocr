"""Cut perspective-corrected, binarized regions out of the images in a folder.

For each image, click the 4 corners of a region (in any order). The region is
warped to an upright rectangle, binarized (Otsu) and saved as
cropped_<id>.png in the output folder (default: a "cropped_images" folder
inside FOLDER). You can cut several regions from one image.
Space: next image, r: clear the clicked points, q (or closing the window): quit.

Standalone tool: install requirements-tools.txt (it needs a GUI build of
OpenCV, opencv-python, not the app's opencv-python-headless).
"""

import argparse
import os
import sys
import uuid

import cv2
import numpy as np

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
WINDOW = "Image"
RESULT_WINDOW = "Cropped Image"


def imread(path):
    # cv2.imread can't open non-ASCII paths on Windows
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def imwrite(path, image):
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


def rectify(image, points):
    """Warp the quadrilateral with corners points to an upright rectangle.
    Returns None if the points don't span an area."""
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
    matrix = cv2.getPerspectiveTransform(corners, target)
    return cv2.warpPerspective(image, matrix, (width, height))


def binarize(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


class PerspectiveCropper:
    def __init__(self, output_folder):
        self.output_folder = output_folder
        self.image = None
        self.points = []

    def set_image(self, image):
        self.image = image
        self.points = []
        self.redraw()

    def reset(self):
        self.points = []
        self.redraw()

    def redraw(self):
        canvas = self.image.copy()
        for point in self.points:
            cv2.circle(canvas, point, 5, (0, 255, 0), -1)
        if len(self.points) > 1:
            cv2.polylines(
                canvas, [np.array(self.points, dtype=np.int32)], True, (0, 255, 0), 2
            )
        cv2.imshow(WINDOW, canvas)

    def on_mouse(self, event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN or self.image is None:
            return
        self.points.append((x, y))
        self.redraw()
        if len(self.points) < 4:
            return
        cropped = rectify(self.image, self.points)
        self.points = []
        if cropped is None:
            print("The 4 points don't span an area, try again")
            return
        thresholded = binarize(cropped)
        cv2.imshow(RESULT_WINDOW, thresholded)
        output_path = os.path.join(self.output_folder, f"cropped_{uuid.uuid4()}.png")
        imwrite(output_path, thresholded)
        print("Saved", output_path)


def window_closed(name):
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("folder", help="folder with the images")
    parser.add_argument(
        "-o",
        "--output",
        help='where to save the crops (default: FOLDER/"cropped_images")',
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.folder):
        parser.error(f"not a folder: {args.folder}")
    images = sorted(
        os.path.join(args.folder, name)
        for name in os.listdir(args.folder)
        if name.lower().endswith(IMAGE_EXTENSIONS)
        and os.path.isfile(os.path.join(args.folder, name))
    )
    if not images:
        print(f"No images in {args.folder}")
        return 1
    output_folder = args.output or os.path.join(args.folder, "cropped_images")
    os.makedirs(output_folder, exist_ok=True)

    cropper = PerspectiveCropper(output_folder)
    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, cropper.on_mouse)
    try:
        for image_path in images:
            image = imread(image_path)
            if image is None:
                print(f"Skipping {image_path}: can't read it as an image")
                continue
            print(f"{image_path}: click 4 corners, space for the next image")
            cropper.set_image(image)
            while True:
                key = cv2.waitKey(50) & 0xFF
                if key == ord(" "):
                    break
                if key == ord("r"):
                    cropper.reset()
                if key == ord("q") or window_closed(WINDOW):
                    return 0
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
