# pdfTranslate — 将棋 PDF → 中文 Markdown

将日文将棋 PDF 电子书转换为「中文翻译文本 + 原版棋盘插图」的 Markdown 文件。

**两步工作流：**
1. **`annotate.py`** — 手动标注棋盘插图位置 + 标记书页码
2. **`translate.py`** — MIMO 多模态模型 OCR + 翻译，输出带书页码的 Markdown

## 依赖

```bash
sudo apt install poppler-utils          # pdftoppm (PDF → PNG)
pip install -r requirements.txt         # Pillow, matplotlib, openai
```

## 快速开始

```bash
# 1. 标注棋盘/插图区域 + 标记首页
python3 annotate.py book.pdf

# 2. 翻译
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json
```

输出在 `book_cn/` 下。

## Step 1 — annotate.py

```bash
python3 annotate.py <pdf路径> [--dpi 200] [--output boards.json]
```

交互操作：

| 按键 | 功能 |
|------|------|
| 鼠标拖拽 | 框选一个棋盘/插图区域 |
| `n` | 下一页（保存当前页标注） |
| `b` | 上一页（保存当前页标注） |
| `d` | 撤销当前页最后一个框 |
| `c` | **从上一页复制标注**（排版固定时快速标注） |
| `m` | **标记/取消当前页为书的第一页**（正文首页） |
| `s` | 跳过（标记为无插图） |
| `q` | 保存并退出 |

### 新功能说明

**`c` — 复制上一页标注**
将棋书的版面通常比较固定，相邻页面的棋盘位置往往相同。按 `c` 可一键复制上一页的所有矩形到当前页，然后微调即可。

**`f` — 标记首页**
棋书的页码通常与 PDF 页码不一致（PDF 前几页是封面、目录等）。翻到正文"第1页"时按 `f`，程序记录此偏移量，最终输出时自动换算为正确的书页码。

标注保存在 `<pdf名>_boards.json`，其中包含：
```json
{
  "_book_first_page": 5,
  "page_1": [[100, 200, 500, 400], ...],
  "page_2": [],
  ...
}
```

## Step 2 — translate.py

```bash
MIMO_API_KEY=sk-xxx python3 translate.py <pdf路径> <boards.json> [选项]
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--dpi` | 200 | 渲染分辨率（发送给 MIMO 的图片 DPI） |
| `--annotation-dpi` | 等于 `--dpi` | 标注时的 DPI（不同时自动缩放坐标） |
| `--api-key` | - | MIMO API key，或设 `MIMO_API_KEY` 环境变量 |
| `--base-url` | `https://api.xiaomimimo.com/v1` | MIMO API 地址 |
| `--model` | mimo-v2.5 | MIMO 模型名 |
| `--max-tokens` | 4096 | 每页最大输出 token 数 |
| `--output-dir` | `<pdf名>_cn/` | 输出目录 |
| `--resume` | - | 从中断处续译（读取进度缓存） |
| `--delay` | 1.0 | 页间 API 调用延迟（秒），避免限流 |
| `--start-page` | 1 | 从指定 PDF 页开始处理 |
| `--end-page` | 全部 | 处理到指定 PDF 页为止 |

### 工作流程

1. 将 PDF 按页渲染为 PNG
2. 根据标注坐标裁剪棋盘区域 → `images/pageN_boardM.jpg`
3. 将棋盘区域涂黑（减少 MIMO 图像 token 消耗）
4. 将涂黑后的页面图片发给 MIMO，由 MIMO 完成 OCR 识别 + 日中翻译
5. 组装输出：翻译文字 + 棋盘插图引用 + `【第x页】` 书页码

### 续译

翻译中断（网络故障、限流等）后可续译：

```bash
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json --resume
```

进度缓存在 `<输出目录>/translate_progress.json`，完成后自动清除。

### 术语翻译

内置将棋术语词库（`glossary.py`），在调用 MIMO 时作为 system prompt 注入，确保：

- 飛車 → 飞车（不会误译为"战车"）
- 居飛車 → 居飞车
- 詰み → 将死
- 手筋 → 手筋（不会直译为"手部肌肉"）
- 保留棋谱符号 ▲ △ 及坐标格式

## 示例

```bash
# 常规流程
python3 annotate.py book.pdf --dpi 200
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json

# 高 DPI 标注 + 低 DPI 翻译（节省 token）
python3 annotate.py book.pdf --dpi 300
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json \
    --dpi 200 --annotation-dpi 300

# 断点续译
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json --resume

# 只翻译第 5-50 页（调试用）
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json \
    --start-page 5 --end-page 50
```

## 输出

```
book_cn/
├── output_cn.md          # 中文翻译 Markdown
└── images/
    ├── page5_board1.jpg  # 棋盘裁剪
    ├── page5_board2.jpg
    └── ...
```

每页以 `【第x页】` 标记书中页码，棋盘插图以 `![board](images/pageN_boardM.jpg)` 引用。

## 文件说明

| 文件 | 用途 |
|------|------|
| `annotate.py` | 交互式标注 GUI |
| `translate.py` | MIMO 翻译管道 |
| `glossary.py` | 将棋术语词库 |
| `process.py` | （保留）旧版 Doc2x OCR 处理流程，与翻译流程无关 |
