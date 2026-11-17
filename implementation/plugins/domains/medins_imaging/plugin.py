"""济南医保影像云索引 插件注册（RT-06 F03）：钩子 + domain.routes 通用挂载。

无采集/发送 job；真实传输能力未确认，投递仅经 target 绑定的 Mock 目的地。
"""
from __future__ import annotations


def register(reg):
    from domains.medins_imaging import hooks as _hooks
    from domains.medins_imaging import routes as _routes
    from plugins.domain_hooks import (HOOK_LEDGER_DETAIL, HOOK_LEDGER_SEARCH,
                                      HOOK_TARGET_SUMMARY)
    reg.attach(HOOK_TARGET_SUMMARY, _hooks.target_summary)
    reg.attach(HOOK_LEDGER_SEARCH, _hooks.ledger_search)
    reg.attach(HOOK_LEDGER_DETAIL, _hooks.ledger_detail)
    reg.attach("domain.routes", _routes.register_routes)
