#!/usr/bin/env python3
"""
Step 2: Black out annotated regions, OCR with v3-2026, and swap images back.

Usage:
    DOC2X_API_KEY=sk-xxx python3 process.py <pdf_path> <boards.json> [--dpi 150]

Workflow:
    1. Convert PDF to page images
    2. Crop and save original board images from annotated coordinates
    3. Black out those regions on each page
    4. Combine blacked-out pages into a temp PDF
    5. Submit to doc2x PDF API (v3-2026 model) for clean OCR
    6. Export to markdown, download zip
    7. Replace black placeholder images in markdown with original board images

Output:
    <pdf_name>_clean/
      output.md          Clean markdown with local board images
      images/            Original board crops
"""

import argparse, json, os, re, io, zipfile, subprocess, glob, time, sys, shutil, requests as rq

BASE_URL = "https://v2.doc2x.noedgeai.com"


def submit_pdf(pdf_path, api_key, model="v3-2026"):
    """Submit PDF via preupload, return uid."""
    headers = {"Authorization": f"Bearer {api_key}"}

    # Preupload
    res = rq.post(f"{BASE_URL}/api/v2/parse/preupload", headers=headers,
                  json={"model": model})
    data = res.json()
    if data.get("code") != "success":
        raise Exception(f"preupload failed: {data}")
    uid = data["data"]["uid"]
    upload_url = data["data"]["url"]

    # Upload
    with open(pdf_path, "rb") as f:
        res = rq.put(upload_url, data=f)
        if res.status_code != 200:
            raise Exception(f"upload failed: {res.text}")

    return uid


def wait_parse(uid, api_key, poll_interval=3, max_wait=600):
    """Poll parse status until success, return result."""
    headers = {"Authorization": f"Bearer {api_key}"}
    elapsed = 0
    while elapsed < max_wait:
        res = rq.get(f"{BASE_URL}/api/v2/parse/status?uid={uid}", headers=headers)
        data = res.json()
        if data.get("code") != "success":
            raise Exception(f"status failed: {data}")
        status = data["data"]
        if status["status"] == "success":
            return status["result"]
        if status["status"] == "failed":
            raise Exception(f"parse failed: {status.get('detail', status)}")
        print(f"    {status.get('progress', '')} ...")
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise Exception(f"parse timeout after {max_wait}s")


def export_and_download(uid, out_dir, api_key, to="md", formula_mode="normal",
                        formula_level=1):
    """Export parse result to markdown, wait, download and extract zip."""
    headers = {"Authorization": f"Bearer {api_key}"}

    # Submit export
    body = {"uid": uid, "to": to, "formula_mode": formula_mode,
            "formula_level": formula_level}
    res = rq.post(f"{BASE_URL}/api/v2/convert/parse", headers=headers, json=body)
    data = res.json()
    if data.get("code") != "success":
        raise Exception(f"export failed: {data}")

    # Wait for export
    for _ in range(60):
        res = rq.get(f"{BASE_URL}/api/v2/convert/parse/result?uid={uid}", headers=headers)
        data = res.json()
        if data.get("code") != "success":
            raise Exception(f"export result failed: {data}")
        d = data["data"]
        if d["status"] == "success":
            download_url = d["url"]
            break
        if d["status"] == "failed":
            raise Exception(f"export failed: {d}")
        time.sleep(2)

    # Download zip
    res = rq.get(download_url)
    zip_path = os.path.join(out_dir, "_temp_export.zip")
    with open(zip_path, "wb") as f:
        f.write(res.content)

    # Extract
    extract_dir = os.path.join(out_dir, "_temp_extract")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    return extract_dir


def main():
    parser = argparse.ArgumentParser(
        description="Black out board regions, OCR with v3-2026, and restore images"
    )
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("boards_json", help="Path to boards.json from annotate.py")
    parser.add_argument("--dpi", type=int, default=300,
                        help="DPI for rendering pages (default: 300, higher = better OCR)")
    parser.add_argument("--annotation-dpi", type=int, default=None,
                        help="DPI used during annotation (defaults to --dpi, set if different)")
    parser.add_argument("--api-key", default=None,
                        help="Doc2x API key (or set DOC2X_API_KEY env var)")
    parser.add_argument("--model", default="v3-2026",
                        help="Doc2x model: v2 or v3-2026 (default: v3-2026)")
    parser.add_argument("--enhance", type=float, default=0,
                        help="Sharpness/contrast boost (default: 0, try 1.5~2.0 for blurry scans)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: <pdf_name>_clean/ next to PDF)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DOC2X_API_KEY")
    if not api_key:
        print("Error: Set DOC2X_API_KEY env var or pass --api-key")
        sys.exit(1)

    pdf_path = os.path.abspath(args.pdf_path)
    boards_path = os.path.abspath(args.boards_json)
    for p in [pdf_path, boards_path]:
        if not os.path.exists(p):
            print(f"Error: not found: {p}")
            sys.exit(1)

    pdf_dir = os.path.dirname(pdf_path)
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]

    with open(boards_path) as f:
        annotations = json.load(f)
    print(f"Loaded {len(annotations)} annotated pages")

    out_dir = args.output_dir or os.path.join(pdf_dir, f"{pdf_name}_clean")
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)

    # Coordinate scaling: if annotation was done at different DPI
    ann_dpi = args.annotation_dpi or args.dpi
    coord_scale = args.dpi / ann_dpi if ann_dpi != args.dpi else 1.0
    if coord_scale != 1.0:
        print(f"Scaling annotation coords: {ann_dpi} -> {args.dpi} DPI (x{coord_scale:.2f})")

    # --- 1. Convert PDF to PNG ---
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
    total = len(png_files)
    print(f"{total} pages")

    # --- 2. Process each page: crop boards, black out, collect blacked-out pages ---
    all_original_boards = {}  # page_num -> [(sx, sy, sw, sh, local_ref), ...]
    blacked_pages = []

    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    for i, png_path in enumerate(png_files):
        page_num = i + 1
        key = f"page_{page_num}"
        page_rects = annotations.get(key, [])
        img = Image.open(png_path).convert("RGB")
        img_w, img_h = img.size
        page_boards = []

        # --- Image enhancement for better OCR ---
        if args.enhance > 0:
            img = ImageEnhance.Sharpness(img).enhance(args.enhance)
            img = ImageEnhance.Contrast(img).enhance(args.enhance)

        for idx, (x, y, w, h) in enumerate(page_rects):
            sx = int(x * coord_scale)
            sy = int(y * coord_scale)
            sw = int(w * coord_scale)
            sh = int(h * coord_scale)
            sw = min(sw, img_w - sx)
            sh = min(sh, img_h - sy)
            if sw <= 0 or sh <= 0:
                continue
            # Save original board
            board_crop = img.crop((sx, sy, sx + sw, sy + sh))
            img_name = f"page{page_num}_board{idx + 1}.jpg"
            board_crop.save(os.path.join(out_dir, "images", img_name), quality=95)
            page_boards.append((sx, sy, sw, sh, f"images/{img_name}"))

        all_original_boards[page_num] = page_boards

        if page_boards:
            # Black out
            draw = ImageDraw.Draw(img)
            for sx, sy, sw, sh, _ in page_boards:
                draw.rectangle([sx, sy, sx + sw, sy + sh], fill="black")

        blacked_pages.append(img)
        n_boards = len(page_boards)
        print(f"  Page {page_num}/{total}: {n_boards} board(s) {'blacked' if n_boards else ''}")

    # --- 3. Combine blacked-out pages into a temp PDF ---
    temp_pdf = os.path.join(out_dir, "_temp_blacked.pdf")
    print(f"\nCombining into PDF: {temp_pdf}")
    blacked_pages[0].save(
        temp_pdf, save_all=True, append_images=blacked_pages[1:],
        resolution=args.dpi,
    )

    # --- 4. Submit to doc2x PDF API (v3-2026) ---
    print(f"Submitting to doc2x PDF API (model={args.model})...")
    uid = submit_pdf(temp_pdf, api_key, model=args.model)
    print(f"  uid: {uid}")

    # --- 5. Wait for parse ---
    print("Parsing...")
    result = wait_parse(uid, api_key)

    # --- 6. Export and download ---
    print("Exporting to markdown...")
    extract_dir = export_and_download(uid, out_dir, api_key)

    # --- 7. Read exported markdown and swap images ---
    md_files = glob.glob(os.path.join(extract_dir, "*.md"))
    if not md_files:
        print("Error: no markdown file found in export")
        sys.exit(1)

    with open(md_files[0], encoding="utf-8") as f:
        md_content = f.read()

    # Find all image references: ![alt](images/xxx.jpg) or <img src="images/xxx.jpg"/>
    img_refs = []
    # Markdown format: ![alt](images/xxx.jpg)
    for m in re.finditer(r'!\[[^\]]*\]\((images/([^)]+\.(?:jpg|jpeg|png|gif|webp)))\)', md_content):
        img_refs.append((m.group(1), m.group(2)))
    # HTML format: <img src="images/xxx.jpg"/>
    for m in re.finditer(r'<img[^>]*src="(images/([^"]+\.(?:jpg|jpeg|png|gif|webp)))"', md_content):
        img_refs.append((m.group(1), m.group(2)))

    print(f"  {len(img_refs)} image references in markdown")

    # Build ordered list of original boards
    all_boards_ordered = []
    for page_num in sorted(all_original_boards.keys()):
        for b in all_original_boards[page_num]:
            all_boards_ordered.append(b)  # (sx, sy, sw, sh, local_ref)

    # Build ordered list of black placeholder images from export
    # Detect black images by checking mean pixel value
    from PIL import Image as PILImage
    black_refs = []  # [(full_ref, img_filename), ...]
    img_dir = os.path.join(extract_dir, "images")
    for full_ref, img_file in img_refs:
        img_path = os.path.join(img_dir, img_file)
        if os.path.exists(img_path):
            try:
                im = PILImage.open(img_path).convert("L")
                # If mean pixel < 30, it's a black rectangle
                if im.resize((1, 1)).getpixel((0, 0)) < 30:
                    black_refs.append((full_ref, img_file))
            except Exception:
                pass

    print(f"  {len(black_refs)} black placeholder images detected")

    # Replace black placeholders with original board images
    for i, (full_ref, img_file) in enumerate(black_refs):
        if i < len(all_boards_ordered):
            _, _, _, _, local_ref = all_boards_ordered[i]
            # Replace both path and alt text in markdown format
            old_md = f']({full_ref})'
            new_md = f']({local_ref})'
            md_content = md_content.replace(old_md, new_md)
            # Also replace alt text
            old_alt = f'![{img_file}]'
            new_alt = f'![board {i + 1}]'
            md_content = md_content.replace(old_alt, new_alt)
            print(f"    {img_file} -> {local_ref}")
        else:
            print(f"    WARNING: extra black image {img_file}, no board to replace")

    # --- 8. Copy remaining (non-board) images to output ---
    for img_path in (glob.glob(os.path.join(extract_dir, "images", "*"))):
        img_name = os.path.basename(img_path)
        dest = os.path.join(out_dir, "images", img_name)
        if not os.path.exists(dest):
            shutil.copy2(img_path, dest)

    # Write final markdown
    output_md = os.path.join(out_dir, "output.md")
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    # --- Cleanup ---
    for tmp in [temp_pdf, os.path.join(out_dir, "_temp_export.zip")]:
        if os.path.exists(tmp):
            os.remove(tmp)
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)

    print(f"\nDone: {output_md}")
    print(f"Images: {os.path.join(out_dir, 'images/')}")


if __name__ == "__main__":
    main()
