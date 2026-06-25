#!/bin/bash
# md → typst → pdf 转换脚本
# 用法：./md2pdf.sh <input.md> [output.pdf] [image_width]
# 默认图片宽度 40%

set -e

INPUT="${1:?Usage: ./md2pdf.sh <input.md> [output.pdf] [image_width]}"
OUTPUT="${2:-${INPUT%.md}.pdf}"
WIDTH="${3:-40%}"
DIR="$(dirname "$INPUT")"
NAME="$(basename "$INPUT" .md)"
TYP="${DIR}/${NAME}.typ"

echo "Input:   $INPUT"
echo "Output:  $OUTPUT"
echo "Width:   $WIDTH"

cd "$DIR"

# 1. 还原 HTML img 为 markdown ![]() 格式
cp "$INPUT" "${INPUT}.bak"
sed -i 's|<img src="\([^"]*\)" width="[^"]*">|![img](\1)|g' "$INPUT"

# 2. md → typst（禁用 implicit_figures 避免 Figure xx: board 标题）
pandoc -f markdown-implicit_figures "$INPUT" -o "$TYP"

# 3. 修复 typst 兼容性 + 设置中文字体
sed -i '1i#set text(font: ("New Computer Modern", "Noto Sans Mono CJK SC"), lang: "zh")\n' "$TYP"
sed -i 's/#horizontalrule/#line(length: 100%)/g' "$TYP"
sed -i "s/alt: \"[^\"]*\")/alt: \"img\", width: ${WIDTH})/g" "$TYP"

# 4. typst → pdf
typst compile "$TYP" "$OUTPUT"

# 5. 恢复原始 md
mv "${INPUT}.bak" "$INPUT"

ls -lh "$OUTPUT"
echo "Done: $OUTPUT"
