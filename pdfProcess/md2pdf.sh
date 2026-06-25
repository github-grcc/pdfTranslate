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

# 3b. 将连续两张图片水平并列放置（跳过空行匹配）
python3 -c "
import re
with open('$TYP') as f:
    lines = f.readlines()
out = []
i = 0
while i < len(lines):
    line = lines[i].rstrip('\n')
    m1 = re.match(r'^#box\(image\((.*)\)\)$', line.strip())
    if m1:
        # Look ahead for second image, skipping empty lines
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        if j < len(lines):
            m2 = re.match(r'^#box\(image\((.*)\)\)$', lines[j].strip())
            if m2:
                out.append(f'#grid(columns: 2, column-gutter: 8pt,')
                out.append(f'  image({m1.group(1)}),')
                out.append(f'  image({m2.group(1)}),')
                out.append(')')
                i = j + 1
                continue
    out.append(line)
    i += 1
with open('$TYP', 'w') as f:
    f.write('\n'.join(out) + '\n')
"

# 4. typst → pdf
typst compile "$TYP" "$OUTPUT"

# 5. 恢复原始 md
mv "${INPUT}.bak" "$INPUT"

ls -lh "$OUTPUT"
echo "Done: $OUTPUT"
