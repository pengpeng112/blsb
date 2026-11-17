"""FB-02 应用签名器真实只读探针（DTR-FIRSTBILL-EXEC-20261021）。

目的：用**本实现** protocol.top_sign/build_form（非官方 C# 参考签名器）构造
一次 query.billstatus 只读请求并直连官方网关，验证平台对本实现签名产物的
实际受理——补齐 EXT-07 证据链最后一环（实现级平台受理）。

安全边界（与 2026-10-20 平台联调同一口径，用户已授权该类只读诊断）：
- 只读查询；bill_code 用随机探针号（从未上传，不可能命中业务单据）；
- 一次请求、零重试、零上传、零患者数据；TLS 校验开启、禁重定向；
- 凭据经 with-credentials.ps1 环境注入，绝不落命令行/回执/日志；
- 回执不含 Secret/签名字段值；排他创建，重跑需新建修订。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IMPL = ROOT / "implementation"
# 回执可带修订后缀（r2/r3…）：首轮结果不覆盖，修 bug 后新建修订再测
_suffix = f"-{sys.argv[1]}" if len(sys.argv) > 1 else ""
RECEIPT = HERE / "evidence" / f"fb02-our-signer-query-receipt{_suffix}.json"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, IMPL / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    if RECEIPT.exists():
        print("回执已存在：不覆盖（新建修订再试）", file=sys.stderr)
        return 2
    for var in ("BLYB_ALIHEALTH_APP_KEY", "BLYB_ALIHEALTH_APP_SECRET",
                "BLYB_ALIHEALTH_REF_ENT_ID"):
        if not os.environ.get(var):
            print(f"缺少环境变量 {var}（用 with-credentials.ps1 包装运行）",
                  file=sys.stderr)
            return 2
    for p in (IMPL / "packages", IMPL / "plugins"):
        sys.path.insert(0, str(p))
    protocol = _load("fb02_protocol",
                     "plugins/destinations/alihealth_drugtrace/protocol.py")

    now_utc = datetime.now(timezone.utc)
    china_now = now_utc + timedelta(hours=8)
    probe_code = "BLYB_FB02_" + uuid4().hex[:16].upper()
    biz = {
        "ref_ent_id": os.environ["BLYB_ALIHEALTH_REF_ENT_ID"],
        "bill_code": probe_code,
        "bill_type": "A",
        "begin_date": china_now.strftime("%Y-%m-%d"),
        "end_date": china_now.strftime("%Y-%m-%d"),
        "page": "1",
        "page_size": "1",
    }
    body, ctype = protocol.build_form(
        "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
        os.environ["BLYB_ALIHEALTH_APP_KEY"],
        os.environ["BLYB_ALIHEALTH_APP_SECRET"],
        biz_params=biz,
        timestamp=china_now.strftime("%Y-%m-%d %H:%M:%S"))

    receipt = {
        "captured_at": now_utc.isoformat(),
        "endpoint": "https://eco.taobao.com/router/rest",
        "route": "Windows_to_official_HTTPS_gateway",
        "method": "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
        "signer": "implementation_protocol_top_sign (FB-02)",
        "probe_bill_code": probe_code,
        "request_count": 0, "real_uploads": 0, "patient_data_sent": False,
        "tls_validation_enabled": True, "redirects_allowed": False,
        "retries": 0, "credentials_or_signatures_in_receipt": False,
        "status": "NOT_SENT",
    }
    # 排他创建：并发/重复执行直接失败，不留半写状态
    fd = os.open(str(RECEIPT), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        receipt["status"] = "RESERVED_OUTCOME_UNCONFIRMED"
        os.write(fd, json.dumps(receipt, ensure_ascii=False).encode())
        req = urllib.request.Request(
            "https://eco.taobao.com/router/rest", data=body,
            headers={"Content-Type": ctype}, method="POST")
        receipt["request_count"] = 1
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            receipt["http_status"] = resp.status
        parsed = protocol.parse_query_response(receipt["http_status"], raw)
        # 只留白名单结果字段；响应原文可能含企业信息，不入共享回执
        receipt["parsed_whitelist"] = {
            "found": parsed.get("found"),
            "total_num": parsed.get("total_num"),
            "unknown": parsed.get("unknown"),
            "error": parsed.get("error"),
            "entry_count": len((parsed.get("bills") or {}).get("list") or []),
        }
        receipt["status"] = "CAPTURED"
        receipt["platform_accepted_signature"] = (
            parsed.get("found") is True
            and not parsed.get("unknown")
            and not parsed.get("error"))
    except Exception as exc:  # noqa: BLE001
        receipt["status"] = "ERROR"
        receipt["error_type"] = type(exc).__name__
        receipt["error_detail"] = str(exc)[:200]
    finally:
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, json.dumps(receipt, ensure_ascii=False, indent=2).encode())
        os.close(fd)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt.get("status") == "CAPTURED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
