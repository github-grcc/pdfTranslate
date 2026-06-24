#!/usr/bin/env python3
"""
Step 2: Translate annotated PDF to Chinese markdown using MIMO multimodal API.

Usage:
    MIMO_API_KEY=sk-xxx python3 translate.py <pdf_path> <boards.json> [options]

Workflow:
    1. Convert PDF to page images
    2. Crop and save original board images from annotated coordinates
    3. Black out board regions on each page
    4. Send each page image to MIMO API for OCR + translation
    5. Assemble translated markdown with board images and 【第x页】 markers

Output:
    <pdf_name>_cn/
      output_cn.md        Translated markdown with book page markers
      images/             Original board crops
"""

import argparse, json, os, subprocess, glob, sys, time, base64, io

# Ensure local imports work regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw


# ── MIMO API client ──────────────────────────────────────

def create_client(api_key: str, base_url: str):
    """Create an OpenAI-compatible client for MIMO API."""
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url)


def translate_page_image(client, model: str, image_b64: str,
                         system_prompt: str, user_prompt: str,
                         max_tokens: int = 4096) -> tuple[str, dict]:
    """
    Send a page image to MIMO for OCR + translation.
    Returns (translated_text, cache_info).
    cache_info = {"cached": N, "total": N} or {}
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            # Text first so prefix cache hits (text is identical across pages)
            {"type": "text", "text": user_prompt},
            # Image varies per page — placed last to minimize cache-break impact
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]},
    ]

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=max_tokens,
    )

    # Extract cache info from response
    cache_info = {}
    try:
        details = resp.usage.prompt_tokens_details
        if details:
            cached = getattr(details, 'cached_tokens', None)
            total = resp.usage.prompt_tokens if resp.usage else None
            if cached is not None:
                cache_info = {"cached": cached, "total": total}
    except Exception:
        pass

    return resp.choices[0].message.content, cache_info


def translate_page_image_with_retry(client, model: str, image_b64: str,
                                    system_prompt: str, user_prompt: str,
                                    max_tokens: int = 4096,
                                    max_retries: int = 3) -> tuple[str, dict]:
    """Call translate_page_image with exponential backoff retry."""
    last_error = None
    for attempt in range(max_retries):
        try:
            text, cache_info = translate_page_image(
                client, model, image_b64,
                system_prompt, user_prompt, max_tokens,
            )
            if text and text.strip():
                return text, cache_info
            # Empty response — retry
            print(f"    Empty response, retrying... ({attempt + 1}/{max_retries})")
            time.sleep(2 ** attempt)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    API error: {e}, retrying in {wait}s... ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                print(f"    API error after {max_retries} retries: {e}")

    raise RuntimeError(f"Translation failed after {max_retries} retries: {last_error}")


# ── Image utilities ──────────────────────────────────────

def image_to_base64(img: Image.Image, fmt: str = "PNG") -> str:
    """Encode a PIL Image to base64 string."""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ── Progress persistence ─────────────────────────────────

def load_progress(progress_file: str) -> dict:
    if os.path.exists(progress_file):
        with open(progress_file, encoding="utf-8") as f:
            return json.load(f)
    return {"completed": {}, "total": 0}


def save_progress(progress_file: str, progress: dict):
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


# ── Page numbering ───────────────────────────────────────

def compute_book_page(pdf_page: int, book_first_page: int | None) -> int | None:
    """Convert PDF page number (1-based) to book page number.
    Returns None for pages before the first book page (cover, TOC).
    """
    if book_first_page is None:
        return None
    book_page = pdf_page - book_first_page + 1
    return book_page if book_page >= 1 else None


# ── Main ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Translate annotated PDF to Chinese markdown using MIMO API"
    )
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("boards_json", help="Path to boards.json from annotate.py")
    parser.add_argument("--dpi", type=int, default=150,
                        help="Resolution for page images sent to MIMO (default: 150)")
    parser.add_argument("--annotation-dpi", type=int, default=300,
                        help="DPI used during annotation (default: 300, matches annotate.py)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: <pdf_name>_cn/ next to PDF)")
    parser.add_argument("--api-key", default=None,
                        help="MIMO API key (or set MIMO_API_KEY env var)")
    parser.add_argument("--base-url", default="https://api.xiaomimimo.com/v1",
                        help="MIMO API base URL (default: https://api.xiaomimimo.com/v1)")
    parser.add_argument("--model", default="mimo-v2.5",
                        help="MIMO model name (default: mimo-v2.5)")
    parser.add_argument("--max-tokens", type=int, default=4096,
                        help="Max output tokens per page (default: 4096)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last saved progress")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay between API calls in seconds (default: 1.0)")
    parser.add_argument("--start-page", type=int, default=1,
                        help="Start from this PDF page (1-based, default: 1)")
    parser.add_argument("--end-page", type=int, default=0,
                        help="End at this PDF page (1-based, default: 0 = all)")

    args = parser.parse_args()

    # ── Setup ────────────────────────────────────────────
    api_key = args.api_key or os.environ.get("MIMO_API_KEY")
    if not api_key:
        print("Error: Set MIMO_API_KEY env var or pass --api-key")
        sys.exit(1)

    pdf_path = os.path.abspath(args.pdf_path)
    boards_path = os.path.abspath(args.boards_json)
    for p in [pdf_path, boards_path]:
        if not os.path.exists(p):
            print(f"Error: not found: {p}")
            sys.exit(1)

    pdf_dir = os.path.dirname(pdf_path)
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]

    # Load annotations
    with open(boards_path) as f:
        annotations = json.load(f)

    book_first_page = annotations.get("_book_first_page")
    if book_first_page:
        print(f"Book first page: PDF page {book_first_page}")
    else:
        print("Note: No book first page marked (use 'f' in annotate.py). "
              "Book page numbers will not be added.")

    # Output dir
    out_dir = args.output_dir or os.path.join(pdf_dir, f"{pdf_name}_cn")
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # Progress file
    progress_file = os.path.join(out_dir, "translate_progress.json")
    progress = load_progress(progress_file) if args.resume else {"completed": {}, "total": 0}

    # ── Coordinate scaling ───────────────────────────────
    ann_dpi = args.annotation_dpi or args.dpi
    coord_scale = args.dpi / ann_dpi if ann_dpi != args.dpi else 1.0
    if coord_scale != 1.0:
        print(f"Scaling annotation coords: {ann_dpi} → {args.dpi} DPI (×{coord_scale:.2f})")

    # ── Convert PDF to PNG ───────────────────────────────
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
    total_pages = len(png_files)
    print(f"{total_pages} pages total")

    # ── Build glossary prompt ────────────────────────────
    from glossary import format_glossary_for_prompt
    glossary_text = format_glossary_for_prompt()

    system_prompt = f"""\
你是一名专业的将棋（日本将棋）书籍翻译专家。你的任务是将日文将棋书页翻译为流畅的中文。

你需要：
1. 仔细观察图片中的日文将棋内容并完整识别
2. 将所有日文翻译为中文
3. 图片中的黑色矩形区域是棋盘/插图，已被移除，翻译时无需提及它们
4. 翻译时严格遵守下方的将棋术语对照表

{glossary_text}
"""

    user_prompt = """\
请将这张将棋书页中的日文内容翻译成中文。

要求：
- 完整翻译页面中所有日文文字
- 图片中的黑色矩形区域是已被移除的棋盘插图，忽略它们
- 页面四角的页码数字是原书页码，不要翻译、不要输出
- 将棋符号 ▲ 转为 ☗（先手），△ 转为 ☖（后手）
- 棋步统一为「☗7六步」「☖3四步」格式（标记+数字+段位+棋子）
- 「同」的场合写「同」，如 ☖同步
- 忽略原文排版换行，按自然段落输出中文，不要保留原文的断行位置
- 保留将棋符号（☗ ☖ 歩 飛 角 金 銀 桂 香 王 玉 と 龍 馬 成 不成 打 同 ）
- 尽量保留原文的段落结构和排版
- 页面中的章节标题、小节标题（如「序章」「2五桂速攻」等）用Markdown加粗：**序章**、**2五桂速攻**
- 直接输出翻译结果，不要添加额外说明或注释
- **禁止使用任何HTML或XML标签**（如<title>、<text>、<div>等），只用纯Markdown格式"""

    # ── Create MIMO client ───────────────────────────────
    client = create_client(api_key, args.base_url)

    # ── Process pages ────────────────────────────────────
    end_page = args.end_page if args.end_page > 0 else total_pages
    translations = []  # List of (pdf_page, book_page, chinese_text, board_refs)

    for i in range(args.start_page - 1, end_page):
        pdf_page = i + 1
        key = f"page_{pdf_page}"

        # Check resume
        if args.resume and str(pdf_page) in progress.get("completed", {}):
            t = progress["completed"][str(pdf_page)]
            translations.append((
                pdf_page, t.get("book_page"),
                t.get("translation", ""),
                t.get("boards", []),
            ))
            print(f"  Page {pdf_page}/{total_pages} — skipped (cached)")
            continue

        page_rects = annotations.get(key, [])

        # Load page image
        img = Image.open(png_files[i]).convert("RGB")

        # ── Apply page crop (trim margins) ─────────────────
        page_crop = annotations.get("_page_crop", None)
        if page_crop:
            cx = int(page_crop[0] * coord_scale)
            cy = int(page_crop[1] * coord_scale)
            cw = int(page_crop[2] * coord_scale)
            ch = int(page_crop[3] * coord_scale)
            # Ensure crop is within image bounds
            cx = max(0, min(cx, img.width - 1))
            cy = max(0, min(cy, img.height - 1))
            cw = min(cw, img.width - cx)
            ch = min(ch, img.height - cy)
            if cw > 0 and ch > 0:
                img = img.crop((cx, cy, cx + cw, cy + ch))
                # Offset for board coordinates (boards are relative to uncropped image)
                coord_offset = (cx, cy)
            else:
                coord_offset = (0, 0)
        else:
            coord_offset = (0, 0)

        img_w, img_h = img.size

        # ── Crop boards & black out ──────────────────────
        board_refs = []
        for idx, (x, y, w, h) in enumerate(page_rects):
            sx = int(x * coord_scale) - coord_offset[0]
            sy = int(y * coord_scale) - coord_offset[1]
            sw = int(w * coord_scale)
            sh = int(h * coord_scale)
            sw = min(sw, img_w - sx)
            sh = min(sh, img_h - sy)
            if sw <= 0 or sh <= 0:
                continue
            # Save board image
            board_img = img.crop((sx, sy, sx + sw, sy + sh))
            img_name = f"page{pdf_page}_board{idx + 1}.jpg"
            board_img.save(os.path.join(images_dir, img_name), quality=95)
            board_refs.append(f"images/{img_name}")

        # Black out board regions
        if board_refs:
            draw = ImageDraw.Draw(img)
            for x, y, w, h in page_rects:
                sx = int(x * coord_scale) - coord_offset[0]
                sy = int(y * coord_scale) - coord_offset[1]
                sw = int(w * coord_scale)
                sh = int(h * coord_scale)
                sx = max(0, sx)
                sy = max(0, sy)
                sw = min(sw, img_w - sx)
                sh = min(sh, img_h - sy)
                if sw > 0 and sh > 0:
                    draw.rectangle([sx, sy, sx + sw, sy + sh], fill="black")

        board_count = len(board_refs)
        print(f"  Page {pdf_page}/{total_pages}: {board_count} board(s) → MIMO...",
              end=" ", flush=True)

        # ── Call MIMO API ────────────────────────────────
        try:
            img_b64 = image_to_base64(img)
            chinese_text, cache_info = translate_page_image_with_retry(
                client, args.model, img_b64,
                system_prompt, user_prompt, args.max_tokens,
            )
            if cache_info:
                cached = cache_info.get("cached", 0)
                total = cache_info.get("total", 0)
                pct = f"{cached / total * 100:.0f}%" if total else "?"
                print(f"OK (cache: {cached}/{total} = {pct})")
            else:
                print("OK")
        except Exception as e:
            print(f"FAILED: {e}")
            chinese_text = f"[翻译失败: {e}]\n\n（原文页面 {pdf_page}）"

        book_page = compute_book_page(pdf_page, book_first_page)
        translations.append((pdf_page, book_page, chinese_text, board_refs))

        # Save progress
        progress["completed"][str(pdf_page)] = {
            "book_page": book_page,
            "translation": chinese_text,
            "boards": board_refs,
        }
        progress["total"] = len(translations)
        save_progress(progress_file, progress)

        # Inter-page delay to avoid rate limiting
        if args.delay > 0 and i < end_page - 1:
            time.sleep(args.delay)

    # ── Assemble output markdown ─────────────────────────
    print(f"\nAssembling translated markdown...")
    md_lines = []

    for pdf_page, book_page, chinese_text, board_refs in translations:
        # Page marker
        if book_page is not None:
            md_lines.append(f"\n\n【第{book_page}页】\n")
        else:
            md_lines.append(f"\n\n---\n")

        # Board images first
        if board_refs:
            for ref in board_refs:
                md_lines.append(f"![board]({ref})")
            md_lines.append("")

        # Translated text
        text = chinese_text.strip()
        md_lines.append(text)

    output_md = os.path.join(out_dir, "output_cn.md")
    with open(output_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines).lstrip())

    # Cleanup empty progress file
    if os.path.exists(progress_file):
        os.remove(progress_file)

    print(f"\nDone: {output_md}")
    print(f"Images: {images_dir}/")
    print(f"Pages translated: {len(translations)}")


if __name__ == "__main__":
    main()
