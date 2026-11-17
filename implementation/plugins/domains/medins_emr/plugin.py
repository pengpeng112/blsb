"""医保电子病历插件注册（LT-07）：仅联调台路由，无采集/发送 job。"""
from __future__ import annotations


def register(reg):
    from domains.medins_emr import routes as _routes
    reg.attach("domain.routes", _routes.register)
