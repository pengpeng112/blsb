# LT-07 泄漏检查（一次性；scratch 约定不入 implementation）：
# 收集原始样例敏感值指纹 → 在 profiles/tests/plugins 中搜索；命中须为 0
#（合成白名单除外）。只输出计数，不打印值。
import json
import os
import re
import sys

ROOT = r"F:\python\blyb"
SPEC_DIR = os.path.join(ROOT, "数据上报", "2、医保电子病历上报")
SCAN = [os.path.join(ROOT, "implementation", "profiles"),
        os.path.join(ROOT, "implementation", "tests"),
        os.path.join(ROOT, "implementation", "plugins")]

# 白名单：合成值/格式串/公共字典（R09：区分敏感样例值与公共常量）
WHITELIST_PATTERNS = [
    r"^\d{4}-\d{2}-\d{2}",           # 日期格式与合成日期
    r"^000000199001010000$",          # 合成证件号
    r"^测试.*$",                        # 合成中文值
    r"^TEST-.*$",
    r"^\d{1,5}$",                     # 短数字（长度/编号）
    r"^(V\d+\.\d+)$",                 # 版本号
    r"^(山东省|济南市|北京市|国家)$",       # 公共行政区划全词（样例地址字段含）
]

SENSITIVE_KEYS = re.compile(
    r"certno|patn_name|psn_name|opter_name|tel|mobile|phone|addr|idcard",
    re.I)


def collect_sample_values():
    vals = set()
    for dirpath, _dirs, files in os.walk(SPEC_DIR):
        for fn in files:
            if not (fn.endswith(".txt") or fn.endswith(".json")):
                continue
            p = os.path.join(dirpath, fn)
            if "README" in fn:
                continue
            raw = open(p, "rb").read()
            text = None
            for enc in ("utf-8", "gbk"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                continue
            try:
                obj = json.loads(text)
            except ValueError:
                continue

            def walk(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if SENSITIVE_KEYS.search(str(k)) and \
                                isinstance(v, str) and len(v) >= 2:
                            vals.add(v)
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(obj)
    return vals


def whitelisted(v: str) -> bool:
    return any(re.match(p, v) for p in WHITELIST_PATTERNS)


def main() -> int:
    vals = collect_sample_values()
    suspects = [v for v in vals if not whitelisted(v)]
    print(f"样例敏感字段取值（去重）：{len(vals)}；白名单外待查：{len(suspects)}")
    hits = 0
    for base in SCAN:
        for dirpath, _d, files in os.walk(base):
            for fn in files:
                if not fn.endswith((".json", ".py", ".md", ".txt", ".vue",
                                    ".ts")):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    text = open(p, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                for v in suspects:
                    if v in text:
                        hits += 1
                        print(f"LEAK? {os.path.relpath(p, ROOT)} 命中样例值"
                              f"（len={len(v)}，前2字符掩码）")
    print(f"总命中数：{hits}（必须为 0）")
    return 0 if hits == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
