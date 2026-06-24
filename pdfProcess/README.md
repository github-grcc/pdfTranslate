# pdfTranslate — 将棋 PDF → 中文 Markdown

将日文将棋 PDF 电子书转换为「中文翻译文本 + 原版棋盘插图」的 Markdown 文件。

**两步工作流：**
1. **`annotate.py`** — 手动标注棋盘插图位置、裁剪页面边距、标记书页码
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

---

## Step 1 — annotate.py

```bash
python3 annotate.py <pdf路径> [--dpi 300] [--output boards.json]
```

### 交互操作

| 按键 | 功能 |
|------|------|
| 鼠标拖拽 | 框选一个棋盘/插图区域（红色矩形） |
| `n` | 下一页（保存当前页标注） |
| `b` | 上一页（保存当前页标注） |
| `d` | 撤销当前页最后一个框 |
| `c` + `1~9` | **将当前页标注存入槽位**（支持 9 个槽位） |
| `1~9` | **从槽位读取标注并应用到当前页** |
| `m` | **标记/取消当前页为书的第一页**（正文首页） |
| `x` | **进入/退出裁剪模式**（拖拽设定内容区域，裁去四周边距） |
| `s` | 跳过（标记为无插图） |
| `q` | 保存并退出 |

### 功能详解

**槽位复制（`c` + 数字）**
将棋书不同章节的版面布局可能不同。按 `c` 再按数字键（1~9），将当前页标注存入对应槽位。之后翻到同布局的页面，直接按数字键即可加载。槽位数据持久化到 boards.json，下次打开标注工具时自动恢复。

**标记首页（`m`）**
棋书的页码通常与 PDF 页码不一致（PDF 前几页是封面、目录等）。翻到正文"第1页"时按 `m`，程序记录此偏移量，翻译时自动换算为正确的书页码。

**页面裁剪（`x`）**
按 `x` 进入裁剪模式，拖拽鼠标绘制绿色虚线矩形框选内容区域（裁去四周空白边距），再按 `x` 退出。裁剪对所有页面生效，翻译时仅发送有效内容区域给 MIMO，节省 token。

### 输出

标注保存在 `<pdf名>_boards.json`：
```json
{
  "_book_first_page": 5,
  "_page_crop": [80, 60, 1100, 1550],
  "_slots": {"1": [[200, 300, 400, 350]], "2": [[...]]},
  "page_1": [[100, 200, 500, 400], ...],
  "page_2": [],
  ...
}
```

---

## Step 2 — translate.py

```bash
MIMO_API_KEY=sk-xxx python3 translate.py <pdf路径> <boards.json> [选项]
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--dpi` | 150 | 发送给 MIMO 的图片 DPI |
| `--annotation-dpi` | 300 | 标注时的 DPI（不同时自动缩放坐标） |
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

1. 将 PDF 按页渲染为 PNG（150 DPI）
2. 应用 _page_crop 裁去页面边距
3. 根据标注坐标裁剪棋盘区域 → `images/pageN_boardM.jpg`
4. 将棋盘区域涂黑（减少 MIMO 图像 token 消耗）
5. 将涂黑后的页面图片 + 术语词库发送给 MIMO，完成 OCR + 翻译
6. 组装输出：棋盘插图 + 翻译文字 + `【第x页】` 书页码

### 翻译特性

- **棋步格式**：▲ → ☗（先手），△ → ☖（后手），格式 `☗7六步` `☖3四步`
- **术语准确**：内置 100+ 条将棋术语词库（`glossary.py`），注入 system prompt
- **标题加粗**：章节标题用 Markdown `**标题**` 格式
- **缓存优化**：system prompt 和 user text 在所有页面中相同，命中 MIMO 前缀缓存
- **缓存日志**：每页翻译后显示缓存命中情况 `(cache: 580/2500 = 23%)`

### 续译

翻译中断（网络故障、限流等）后可续译：

```bash
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json --resume
```

进度缓存在 `<输出目录>/translate_progress.json`，完成后自动清除。

---

## 示例

```bash
# 常规流程
python3 annotate.py book.pdf
MIMO_API_KEY=sk-xxx python3 translate.py book.pdf book_boards.json

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

每页以 `【第x页】` 标记书中页码，棋盘插图前置，翻译文字紧随其后。

## 文件说明

| 文件 | 用途 |
|------|------|
| `annotate.py` | 交互式标注 GUI（棋盘框选、首页标记、页面裁剪、槽位复制） |
| `translate.py` | MIMO 多模态翻译管道（PDF→图片→API→Markdown） |
| `glossary.py` | 将棋术语词库（100+ 条，注入 LLM system prompt） |
| `process.py` | （保留）旧版 Doc2x OCR 处理流程，与翻译流程无关 |
