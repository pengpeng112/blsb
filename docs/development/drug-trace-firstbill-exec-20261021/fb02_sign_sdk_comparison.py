"""FB-02 签名算法官方 SDK 对照（DTR-FIRSTBILL-EXEC-20261021）。

对照对象（两套独立官方来源 + 一套官方文档向量）：
1. 官方 PyPI TOP SDK `Top-1.0.0`（taobao open platform Python SDK；
   sha256=9ca70d8368baf0fb58da09334b88713437537ca2fe989a5a64d88d5d52b8ea82，
   存 implementation/var/signature-ref/Top-1.0.0.zip，不入 Git）的
   `top/api/base.py::sign(secret, parameters)`——运行时从 zip 动态加载，
   不复制源码进仓库。
2. 官方 C# 签名参考（developer.alibaba.com articleId=107727，上棒已提取
   OfficialTopReference.cs 并修齐空值过滤；其公开向量见
   platform-live-test-20261020/evidence/public-reference-vectors.json）。
3. 官方文档示例向量（aliexpress logistics 示例，secret=helloworld 公开示例）。

对照维度：键排序、UTF-8 中文、前导零、可选 session、空值边界、表单编码与
签名的关系（签名覆盖原始参数，不覆盖百分号编码后的串）。

只使用公开示例/合成参数；真实 AppSecret 绝不进入本对照或测试夹具。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IMPL = ROOT / "implementation"
SDK_ZIP = IMPL / "var/signature-ref/Top-1.0.0.zip"
SDK_SHA256 = "9ca70d8368baf0fb58da09334b88713437537ca2fe989a5a64d88d5d52b8ea82"


def load_official_sdk_sign():
    """从官方 SDK zip 动态加载 sign 函数（不落源码副本）。

    官方 Top-1.0.0 为 Python2 代码：`parameters.keys()` 返回 list 后调
    `.sort()`，且 md5 直接接受 str。为在 py3 下运行做两处**语义等价**适配并
    在证据中记录：`keys()`+`sort()` → `sorted(parameters)`；md5 前对 str 做
    UTF-8 编码（TOP 签名口径）。拼接公式与执行顺序保持官方原码。
    """
    import re
    zf = zipfile.ZipFile(SDK_ZIP)
    src = zf.read("Top-1.0.0/top/api/base.py").decode("utf-8")
    m = re.search(r"\ndef sign\(.*?(?=\ndef |\nclass )", src, re.S)
    assert m, "official sign function not found in SDK"
    official_src = m.group(0)
    adapted = official_src.replace(
        "keys = parameters.keys()\n        keys.sort()",
        "keys = sorted(parameters)")
    assert adapted != official_src, "py2 sort pattern not found"
    class _Py3Hashlib:  # py3 适配垫片：str → UTF-8 编码后 md5
        md5 = staticmethod(lambda s: hashlib.md5(
            s.encode("utf-8") if isinstance(s, str) else s))
    ns: dict = {"hashlib": _Py3Hashlib}
    exec(adapted, ns)  # noqa: S102 —— 对照目的：执行官方原码（最小 py3 适配）
    return official_src, ns["sign"]


def our_sign(params: dict[str, str], secret: str) -> str:
    import sys
    for p in (IMPL / "packages", IMPL / "plugins"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    spec = importlib.util.spec_from_file_location(
        "dt_protocol", IMPL / "plugins/destinations/alihealth_drugtrace/protocol.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.top_sign(params, secret)


VECTORS = [
    {   # 官方文档公开示例（articleId=107727 文内示例参数，secret=helloworld）
        "id": "official_article_example",
        "secret": "helloworld",
        "params": {
            "session": "test", "v": "2.0",
            "method": "aliexpress.logistics.redefining.getonlinelogisticsinfo",
            "timestamp": "2016-01-01 12:00:00",
            "format": "json", "sign_method": "md5", "app_key": "12345678",
            "international_logistics_id": "LP00038357949881",
            "logistics_status": "INIT"},
    },
    {   # 中文 + 空格 + 保留字符 + 前导零（合成；两实现应一致）
        "id": "unicode_and_leading_zeros",
        "secret": "public_offline_demo",
        "params": {
            "method": "example.only", "bill_code": "000123",
            "note": "药品+&空 格", "trace_codes": "00000000000000000001"},
    },
    {   # 全 ASCII 常规参数 + session（TOP 常规流量形状）
        "id": "typical_retail_query_shape",
        "secret": "synthetic_secret_for_comparison",
        "params": {
            "method": "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
            "app_key": "12345678", "timestamp": "2026-10-21 12:00:00",
            "format": "json", "v": "2.0", "sign_method": "md5",
            "session": "optional_session_token",
            "ref_ent_id": "REF000001", "bill_code": "DTRT-0000ABCD",
            "bill_type": "A", "begin_date": "2026-10-01", "end_date": "2026-10-21",
            "page": "1", "page_size": "20"},
    },
    {   # 上传形状：多码逗号拼接 + 中文数量单位（合成）
        "id": "upload_retail_shape",
        "secret": "synthetic_secret_for_comparison",
        "params": {
            "method": "alibaba.alihealth.drugtrace.top.yljg.uploadretail",
            "app_key": "12345678", "timestamp": "2026-10-21 12:00:00",
            "format": "json", "v": "2.0", "sign_method": "md5",
            "bill_code": "DTRT-0000ABCD", "bill_type": "321",
            "bill_time": "2026-10-20 08:30:00", "ref_user_id": "REF000001",
            "trace_codes": "81000001,81000002,81000003"},
    },
]


def main() -> int:
    import hashlib as _h
    zip_sha = _h.sha256(SDK_ZIP.read_bytes()).hexdigest()
    assert zip_sha == SDK_SHA256, f"SDK zip hash changed: {zip_sha}"
    sign_src, official_sign = load_official_sdk_sign()
    results = []
    agree = True
    for vec in VECTORS:
        ours = our_sign(vec["params"], vec["secret"])
        # 官方 SDK sign 签名是 sign(secret, parameters)——注意参数顺序。
        # 官方实现不做空值过滤：对照仅在无空值向量上断言一致；
        # 本组向量均无空值（空值边界差异在报告中单列，见 KNOWN_DIVERGENCE）。
        has_empty = any(k == "" or v == "" for k, v in vec["params"].items())
        official = official_sign(vec["secret"], dict(vec["params"])) \
            if not has_empty else None
        row = {"id": vec["id"], "has_empty_value": has_empty,
               "ours": ours, "official_sdk": official,
               "match": (ours == official) if official else None}
        results.append(row)
        if official and ours != official:
            agree = False
    evidence = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "sdk": {
            "name": "Top (PyPI, taobao open platform Python SDK)",
            "version": "1.0.0",
            "source": "https://pypi.org/project/Top/",
            "zip_sha256": SDK_SHA256,
            "sign_function": "top/api/base.py::sign",
            "sign_source_sha256": _h.sha256(sign_src.encode()).hexdigest(),
        },
        "csharp_reference": {
            "source": ("developer.alibaba.com docs doc.htm articleId=107727"
                       "（平台联调 2026-10-20 已提取对照并修齐空值过滤）"),
            "vectors": "../platform-live-test-20261020/evidence/"
                       "public-reference-vectors.json",
        },
        "vectors": results,
        "known_divergence": [
            "官方 PyPI Top SDK（Python2 时代）sign 不过滤空参数名/空字符串值；"
            "官方 C# 参考跳过空项。本项目按 C# 参考实现空值过滤——build_form "
            "的全部必填业务参数经 build_biz_params 校验非空，实际流量不含空值，"
            "两实现对本项目真实流量等价。",
            "表单编码：SDK 用 urlencode(quote_plus)；本项目用 quote(safe='')"
            "（%20）。签名均覆盖原始参数而非编码串，编码差异不影响签名与TOP解析。",
            "时间戳：SDK 用毫秒 epoch；官方签名文档示例与本项目用"
            " yyyy-MM-dd HH:mm:ss（GMT+8 墙钟显式生成）。TOP 网关两种均接受。",
        ],
        "conclusion": "non-empty vectors agree" if agree else "MISMATCH",
    }
    out = HERE / "evidence" / "fb02-sign-sdk-comparison.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps(evidence["vectors"], ensure_ascii=False, indent=1))
    print("conclusion:", evidence["conclusion"])
    return 0 if agree else 1


if __name__ == "__main__":
    raise SystemExit(main())
