import re
pat = r'^[/／]?(?:画|绘图|绘画|生图|画图|作画|画画)(?:\s+(.+))?$'
tests = ['画风成熟点，再来', '画 一个女孩', '画 真人 一个女孩', '生图 一只猫',
         '作画', '绘图 一个女孩', '画真人 一个女孩', '/画 真人 一个女孩', '画画']
with open('_tmp_regex_out.txt', 'w', encoding='utf-8') as f:
    for t in tests:
        m = re.match(pat, t.strip(), re.S)
        f.write(f"{t!r} -> {m.group(1) if m else None}\n")
