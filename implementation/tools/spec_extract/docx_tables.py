"""docx 表格提取（LT-07）：python-docx 优先；输出与最近标题关联的表结构。

输出不含任何样例值——只提取结构（表头/行/标题路径）。
GBK/编码兜底不适用（docx 为 zip+xml）。
"""
from __future__ import annotations

import re

TXN_RE = re.compile(r"\b(1\d{4})\b")


def extract_tables(docx_path: str) -> list[dict]:
    """docx → [{"heading_path": [...], "table_index": i, "header": [...],
    "rows": [[...]]}]（表格按文档序，关联最近出现的标题链）。"""
    import docx
    doc = docx.Document(docx_path)
    # 段落序→标题栈；表格锚定在其前最近标题
    events: list[tuple[int, object]] = []   # (kind, payload)：0=标题(层级,文本) 1=表
    for p in doc.paragraphs:
        if p.style.name.startswith("Heading") and p.text.strip():
            events.append((0, (int(p.style.name.split()[-1]), p.text.strip())))
    # python-docx 无统一 body 流序 API——用 XML 顺序遍历
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    heads: list[tuple[int, str]] = []
    out: list[dict] = []
    ti = 0
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            if p.style.name.startswith("Heading") and p.text.strip():
                heads.append((int(p.style.name.split()[-1]), p.text.strip()))
        elif tag == "tbl":
            t = Table(child, doc)
            header = [c.text.strip() for c in t.rows[0].cells] if t.rows else []
            rows = [[c.text.strip() for c in r.cells] for r in t.rows[1:]]
            out.append({"table_index": ti,
                        "heading_path": [h[1] for h in heads[-6:]],
                        "header": header, "rows": rows})
            ti += 1
    return out


def txn_of_heading(heading: str) -> str | None:
    m = TXN_RE.search(heading)
    return m.group(1) if m else None


def is_param_table(header: list[str]) -> bool:
    j = "".join(header)
    return ("参数代码" in j or "数据元标识" in j or "字段名" in j) and \
        ("类型" in j or "长度" in j)


def txn_transaction_list(tables: list[dict]) -> list[dict]:
    """接口清单表（表头含 接口编号/接口名称）。"""
    for t in tables:
        if "接口编号" in "".join(t["header"]) and "接口名称" in "".join(t["header"]):
            idx = t["header"].index("接口编号")
            nm = t["header"].index("接口名称")
            out = []
            for r in t["rows"]:
                if len(r) > max(idx, nm) and TXN_RE.fullmatch(r[idx] or ""):
                    out.append({"code": r[idx], "name": r[nm]})
            return out
    return []
