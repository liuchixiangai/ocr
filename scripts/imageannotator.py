"""Type the ground-truth text of each image in a folder.

Shows the images (.png, .jpg, .jpeg) in FOLDER that have no <name>.gt.txt yet,
oldest first. Type the text in the image and press Enter: it is saved as
<name>.gt.txt (UTF-8, the tesstrain ground-truth format) and the next image is
shown. Escape quits; run it again to continue where you left off.

Standalone tool: install requirements-tools.txt (Pillow; tkinter ships with
Python).
"""

import argparse
import os
import sys
import tkinter as tk

from PIL import Image, ImageTk

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
CANVAS_SIZE = 500


def ground_truth_path(image_path):
    return os.path.splitext(image_path)[0] + ".gt.txt"


def images_to_annotate(folder):
    """The images in folder without a .gt.txt file, oldest first."""
    images = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(IMAGE_EXTENSIONS)
    ]
    images = [
        path
        for path in images
        if os.path.isfile(path) and not os.path.exists(ground_truth_path(path))
    ]
    images.sort(key=lambda path: (os.path.getctime(path), path))
    return images


class TextExtractor:
    def __init__(self, master, images):
        self.master = master
        self.images = images
        self.current_image_index = 0
        self.saved = 0

        self.label = tk.Label(master, text="Enter the text from the image:")
        self.label.pack()

        self.text_entry = tk.Entry(master, width=50)
        self.text_entry.pack()
        self.text_entry.bind("<Return>", self.save_text)
        self.text_entry.focus_set()

        self.canvas = tk.Canvas(master, width=CANVAS_SIZE, height=CANVAS_SIZE)
        self.canvas.pack()

        # Bind Escape key to close the program
        self.master.bind("<Escape>", lambda event: self.master.quit())

        self.show_current_image()

    @property
    def current_image(self):
        if self.current_image_index < len(self.images):
            return self.images[self.current_image_index]
        return None

    def show_current_image(self):
        image_path = self.current_image
        self.canvas.delete("all")
        try:
            with Image.open(image_path) as img:
                img.thumbnail((CANVAS_SIZE, CANVAS_SIZE), Image.LANCZOS)
                self.tk_image = ImageTk.PhotoImage(img)
        except OSError as e:
            print(f"Can't open {image_path}: {e}")
            self.tk_image = None
        if self.tk_image is not None:
            self.canvas.create_image(
                CANVAS_SIZE // 2, CANVAS_SIZE // 2, image=self.tk_image
            )
        self.master.title(
            f"Image Text Extractor - Image {self.current_image_index + 1}"
            f"/{len(self.images)} - {os.path.basename(image_path)}"
        )

    def save_text(self, event=None):
        text = self.text_entry.get()
        if text and self.current_image:
            with open(
                ground_truth_path(self.current_image), "w", encoding="utf-8"
            ) as f:
                f.write(text)
            self.saved += 1
            self.text_entry.delete(0, tk.END)
            self.next_image()

    def next_image(self):
        self.current_image_index += 1
        if self.current_image is not None:
            self.show_current_image()
        else:
            self.master.quit()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("folder", help="folder with the images to annotate")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.folder):
        parser.error(f"not a folder: {args.folder}")
    images = images_to_annotate(args.folder)
    if not images:
        print(f"Nothing to annotate: every image in {args.folder} has a .gt.txt")
        return 0

    root = tk.Tk()
    app = TextExtractor(root, images)
    root.mainloop()
    print(f"Saved {app.saved} annotation(s), {len(images) - app.saved} left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
