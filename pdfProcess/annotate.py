#!/usr/bin/env python3
"""
Step 1: Manually annotate board/figure regions to exclude from OCR.

Usage:
    python3 annotate.py <pdf_path> [--dpi 150] [--output boards.json]

Controls:
    Click & drag  = draw rectangle around board/figure
    n             = next page
    b             = previous page
    d             = delete last rectangle on current page
    c             = copy rectangles from previous page
    m             = mark/unmark current page as first book page
    s             = skip page (mark as no boards)
    q             = quit and save annotations

Output:
    boards.json: {"page_N": [[x, y, w, h], ...], ...}
"""

import argparse, json, os, subprocess, glob, sys
from PIL import Image
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    parser = argparse.ArgumentParser(description="Annotate board/figure regions in a PDF")
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("--dpi", type=int, default=300, help="Resolution for page images (default: 300)")
    parser.add_argument("--output", default=None, help="Output annotation JSON path (default: boards.json next to PDF)")
    parser.add_argument("--export-images", action="store_true",
                        help="Export cropped board images from existing annotations and exit")
    args = parser.parse_args()

    pdf_path = os.path.abspath(args.pdf_path)
    if not os.path.exists(pdf_path):
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    pdf_dir = os.path.dirname(pdf_path)
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_file = args.output or os.path.join(pdf_dir, f"{pdf_name}_boards.json")

    # Load existing annotations if any
    annotations = {}
    if os.path.exists(output_file):
        with open(output_file) as f:
            annotations = json.load(f)
        print(f"Loaded {len(annotations)} existing annotations from {output_file}")

    # Convert PDF to PNG
    png_dir = os.path.join(pdf_dir, f"{pdf_name}_pages_{args.dpi}dpi")
    os.makedirs(png_dir, exist_ok=True)
    if not glob.glob(os.path.join(png_dir, "page-*.png")):
        print(f"Converting PDF to PNG ({args.dpi} DPI)...")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(args.dpi), pdf_path,
             os.path.join(png_dir, "page")],
            check=True,
        )
    png_files = sorted(glob.glob(os.path.join(png_dir, "page-*.png")))

    # ── Export images mode ─────────────────────────────────
    if args.export_images:
        page_keys = [k for k in annotations if k.startswith("page_")]
        if not page_keys:
            print("Error: no page annotations found. Annotate first, then export.")
            sys.exit(1)

        from PIL import Image as PILImage
        out_dir = os.path.join(pdf_dir, f"{pdf_name}_boards_export")
        os.makedirs(out_dir, exist_ok=True)

        # Load page_crop if present
        page_crop = annotations.get("_page_crop", None)
        cx, cy, cw, ch = page_crop if page_crop else (0, 0, 0, 0)

        png_files = sorted(glob.glob(os.path.join(png_dir, "page-*.png")))
        total_exported = 0

        for i, png_path in enumerate(png_files):
            page_num = i + 1
            key = f"page_{page_num}"
            page_rects = annotations.get(key, [])
            if not page_rects:
                continue

            img = PILImage.open(png_path).convert("RGB")
            # Apply page crop
            if page_crop and cw > 0 and ch > 0:
                img = img.crop((cx, cy, cx + cw, cy + ch))

            for idx, (x, y, w, h) in enumerate(page_rects):
                sx = x - cx
                sy = y - cy
                sx = max(sx, 0)
                sy = max(sy, 0)
                sw = min(w, img.width - sx)
                sh = min(h, img.height - sy)
                if sw <= 0 or sh <= 0:
                    continue
                board_img = img.crop((sx, sy, sx + sw, sy + sh))
                img_name = f"page{page_num}_board{idx + 1}.jpg"
                board_img.save(os.path.join(out_dir, img_name), quality=95)
                total_exported += 1
                print(f"  Page {page_num} board {idx + 1}: {img_name}")

        print(f"\nExported {total_exported} board images to {out_dir}/")
        sys.exit(0)

    total = len(png_files)
    print(f"{total} pages")

    # State
    current_idx = 0
    page_rects = []
    copy_mode = False   # When True, next digit key (1-9) saves to slot
    crop_mode = False   # When True, drag draws a green page-crop rect (applies to ALL pages)
    slots = annotations.get("_slots", {})  # type: dict — {1: [[x,y,w,h],...], ...}
    # Convert string keys from JSON back to int
    slots = {int(k): v for k, v in slots.items()}

    # Load existing page crop (content region to keep, cuts margins)
    page_crop = annotations.get("_page_crop", None)  # [x, y, w, h] or None

    fig, ax = plt.subplots(figsize=(10, 14))
    fig.canvas.manager.set_window_title(
        f"Board Annotator - {os.path.basename(pdf_path)}  |  "
        "n=next b=back d=undo c+1-9=save-slot 1-9=load-slot m=first-page x=crop s=skip q=quit"
    )

    def load_page(idx):
        nonlocal page_rects
        key = f"page_{idx + 1}"
        page_rects = annotations.get(key, [])[:]
        img = Image.open(png_files[idx])
        ax.clear()
        ax.imshow(img)
        title = f"Page {idx + 1}/{total}  |  Rectangles: {len(page_rects)}"
        if crop_mode:
            title += "  |  [CROP MODE] drag to set content area"
        bp = annotations.get("_book_first_page")
        if bp:
            title += f"  |  Book first page: P{bp}"
            if bp == idx + 1:
                title += "  <== THIS"
        ax.set_title(title)
        ax.axis("off")
        for x, y, w, h in page_rects:
            ax.add_patch(Rectangle((x, y), w, h, fill=False, edgecolor="red", linewidth=2))
        # Show crop region if defined
        if page_crop:
            cx, cy, cw, ch = page_crop
            ax.add_patch(Rectangle((cx, cy), cw, ch, fill=False,
                                   edgecolor="lime", linewidth=2, linestyle="--"))
        fig.canvas.draw()

    def save():
        annotations["_slots"] = {str(k): v for k, v in slots.items()}
        if page_crop is not None:
            annotations["_page_crop"] = list(page_crop)
        else:
            annotations.pop("_page_crop", None)
        with open(output_file, "w") as f:
            json.dump(annotations, f, indent=2)
        print(f"  Saved: {output_file}")

    start_xy = [None]

    def on_press(event):
        if event.inaxes == ax and event.button == 1:
            start_xy[0] = (event.xdata, event.ydata)

    def on_release(event):
        nonlocal page_crop
        if event.inaxes != ax or start_xy[0] is None:
            return
        if event.button == 1:
            x0, y0 = start_xy[0]
            x1, y1 = event.xdata, event.ydata
            if abs(x1 - x0) > 20 and abs(y1 - y0) > 20:
                x, y = min(x0, x1), min(y0, y1)
                w, h = abs(x1 - x0), abs(y1 - y0)
                if crop_mode:
                    # Set the page crop (content region to keep)
                    page_crop = (int(x), int(y), int(w), int(h))
                    load_page(current_idx)
                else:
                    page_rects.append((int(x), int(y), int(w), int(h)))
                    ax.add_patch(Rectangle((x, y), w, h, fill=False, edgecolor="red", linewidth=2))
                    ax.set_title(f"Page {current_idx + 1}/{total}  |  Rectangles: {len(page_rects)}")
                    fig.canvas.draw()
            start_xy[0] = None

    def on_key(event):
        nonlocal current_idx, copy_mode, crop_mode
        key = f"page_{current_idx + 1}"
        if event.key == "n":
            annotations[key] = page_rects[:]
            save()
            current_idx = min(current_idx + 1, total - 1)
            load_page(current_idx)
        elif event.key == "b":
            annotations[key] = page_rects[:]
            save()
            current_idx = max(current_idx - 1, 0)
            load_page(current_idx)
        elif event.key == "d":
            if page_rects:
                page_rects.pop()
                annotations[key] = page_rects[:]
                load_page(current_idx)
        elif event.key == "c":
            copy_mode = True
            ax.set_title(
                f"Page {current_idx + 1}/{total}  |  "
                f"Rectangles: {len(page_rects)}  |  Press 1-9 to save to slot"
            )
            fig.canvas.draw()
        elif event.key in "123456789":
            digit = int(event.key)
            if copy_mode:
                # Save current page rects to slot
                slots[digit] = page_rects[:]
                copy_mode = False
                annotations[key] = page_rects[:]
                save()
                ax.set_title(
                    f"Page {current_idx + 1}/{total}  |  "
                    f"Rectangles: {len(page_rects)}  |  Saved to slot {digit}"
                )
                fig.canvas.draw()
            else:
                # Load from slot
                if digit in slots:
                    annotations[key] = [list(r) for r in slots[digit]]
                    load_page(current_idx)
                    ax.set_title(
                        f"Page {current_idx + 1}/{total}  |  "
                        f"Rectangles: {len(page_rects)}  |  Loaded from slot {digit}"
                    )
                    fig.canvas.draw()
                else:
                    print(f"  Slot {digit} is empty")
        elif event.key == "m":
            old_first = annotations.get("_book_first_page")
            if old_first == current_idx + 1:
                del annotations["_book_first_page"]
                banner = "unmarked as first book page"
            else:
                annotations["_book_first_page"] = current_idx + 1
                banner = f"marked as first book page"
            annotations[key] = page_rects[:]
            save()
            ax.set_title(
                f"Page {current_idx + 1}/{total}  |  "
                f"Rectangles: {len(page_rects)}  |  [{banner}]"
            )
            fig.canvas.draw()
        elif event.key == "x":
            # Toggle crop mode
            crop_mode = not crop_mode
            annotations[key] = page_rects[:]
            save()
            load_page(current_idx)
        elif event.key == "s":
            annotations[key] = []
            save()
            current_idx = min(current_idx + 1, total - 1)
            load_page(current_idx)
        elif event.key == "q":
            annotations[key] = page_rects[:]
            save()
            print(f"Done. Annotations saved to {output_file}")
            plt.close()
            sys.exit(0)

    fig.canvas.mpl_connect("button_press_event", on_press)
    fig.canvas.mpl_connect("button_release_event", on_release)
    fig.canvas.mpl_connect("key_press_event", on_key)

    load_page(0)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
