"""Crop rectangular regions out of the images in a folder.

Drag a rectangle over the image to save that region as cropped_<id>.png in the
output folder (default: an "out" folder inside FOLDER). Right or n: next image,
Left or p: previous image, Escape: cancel the rectangle being drawn, q: quit.

Standalone tool: install requirements-tools.txt (Pillow; tkinter ships with
Python).
"""

import argparse
import os
import sys
import tkinter as tk
import uuid

from PIL import Image, ImageTk

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def list_images(folder):
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(IMAGE_EXTENSIONS)
        and os.path.isfile(os.path.join(folder, name))
    )


class ImageCropper:
    def __init__(self, master, image_paths, output_folder):
        self.master = master
        self.master.focus_force()  # give focus to the window
        self.canvas = tk.Canvas(master, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.image_paths = image_paths
        self.output_folder = output_folder
        self.current_image_index = 0
        self.loaded_index = None
        self.current_image = None
        # canvas pixels per image pixel of the displayed image
        self.display_scale = 1.0
        self.start_x = None
        self.start_y = None
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)
        master.bind("<Right>", self.next_image)
        master.bind("n", self.next_image)
        master.bind("<Left>", self.previous_image)
        master.bind("p", self.previous_image)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        master.bind("q", self.close_program)
        master.bind("<Escape>", self.cancel_rect)

        # Set the default window size
        master.geometry("640x480")

    def cancel_rect(self, event=None):
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = None
        self.start_x = None
        self.start_y = None

    def close_program(self, event=None):
        self.master.destroy()

    def on_canvas_resize(self, event=None):
        self.display_image(self.current_image_index)

    def load_image(self, index):
        if index == self.loaded_index:
            return self.current_image is not None
        self.loaded_index = index
        path = self.image_paths[index]
        try:
            with Image.open(path) as image:
                image.load()
                self.current_image = image.copy()
        except OSError as e:
            print(f"Can't open {path}: {e}")
            self.current_image = None
        return self.current_image is not None

    def display_image(self, index):
        if not 0 <= index < len(self.image_paths):
            return
        self.current_image_index = index
        self.cancel_rect()
        self.canvas.delete("all")
        name = os.path.basename(self.image_paths[index])
        self.master.title(
            f"Image Cropper - {index + 1}/{len(self.image_paths)} - {name}"
        )
        if not self.load_image(index):
            return

        window_width = self.canvas.winfo_width()
        window_height = self.canvas.winfo_height()
        if window_width > 1 and window_height > 1:
            img_width, img_height = self.current_image.size
            scale = min(window_width / img_width, window_height / img_height)
            new_size = (max(1, int(img_width * scale)), max(1, int(img_height * scale)))
            self.display_scale = scale

            resized_image = self.current_image.resize(new_size, Image.LANCZOS)
            self.tk_image = ImageTk.PhotoImage(resized_image)
            self.canvas.create_image(0, 0, image=self.tk_image, anchor="nw")
            self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def on_button_press(self, event):
        self.cancel_rect()
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y, outline="red"
        )

    def on_move_press(self, event):
        if self.rect is None or self.start_x is None:
            return
        cur_x, cur_y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_button_release(self, event):
        if self.rect is None or self.start_x is None:
            return
        end_x, end_y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.save_crop(self.start_x, self.start_y, end_x, end_y)
        self.cancel_rect()

    def canvas_to_image_coords(self, x, y):
        return int(x / self.display_scale), int(y / self.display_scale)

    def save_crop(self, x1, y1, x2, y2):
        if self.current_image is None:
            return
        img_width, img_height = self.current_image.size
        # transform to image coordinates
        x1, y1 = self.canvas_to_image_coords(x1, y1)
        x2, y2 = self.canvas_to_image_coords(x2, y2)
        # make sure x1,y1 is top left and x2,y2 is bottom right
        x1, x2 = sorted([x1, x2])
        y1, y2 = sorted([y1, y2])
        # make sure the coordinates are within the image bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img_width, x2), min(img_height, y2)

        # Ensure valid crop area
        if x2 > x1 and y2 > y1:
            cropped = self.current_image.crop((x1, y1, x2, y2))
            os.makedirs(self.output_folder, exist_ok=True)
            unique_id = str(uuid.uuid4())[:8]
            save_path = os.path.join(self.output_folder, f"cropped_{unique_id}.png")
            cropped.save(save_path)
            print(f"Saved {save_path}")

    def next_image(self, event=None):
        if self.current_image_index < len(self.image_paths) - 1:
            self.display_image(self.current_image_index + 1)

    def previous_image(self, event=None):
        if self.current_image_index > 0:
            self.display_image(self.current_image_index - 1)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("folder", help="folder with the images to crop")
    parser.add_argument(
        "-o", "--output", help='where to save the crops (default: FOLDER/"out")'
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.folder):
        parser.error(f"not a folder: {args.folder}")
    image_paths = list_images(args.folder)
    if not image_paths:
        print(f"No .png/.jpg/.jpeg images in {args.folder}")
        return 1

    root = tk.Tk()
    ImageCropper(root, image_paths, args.output or os.path.join(args.folder, "out"))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
