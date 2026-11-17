"""病理领域插件（W11 manifest 注册）。

由 app_runtime.load_registry 按需加载；register() 把病理节点处理器、
编号迁移与 Runtime 构建器登记进 PluginRegistry。metrics-only 进程
不加载本模块，从而不创建病理表、不执行病理逻辑。
"""
from __future__ import annotations

PLUGIN_KEY = "pathology"
CAPABILITIES = ["resolve", "quality", "completeness", "freeze", "runtime_build"]


def register(reg) -> None:
    from domains.pathology.models import migration as pathology_migration
    from domains.pathology.handlers import (build_runtime, h_pathology_completeness,
                                            h_pathology_freeze, h_pathology_quality,
                                            h_pathology_resolve,
                                            synthetic_warehouse)
    reg.add_handlers({
        ("resolve", "pathology.resolve"): h_pathology_resolve,
        ("resolve", "pathology.completeness"): h_pathology_completeness,
        ("validate", "pathology.quality"): h_pathology_quality,
        ("wait_dependencies", "pathology.completeness"): h_pathology_completeness,
        ("package.freeze", "core.package"): h_pathology_freeze,  # 病理流程的冻结节点
    })
    reg.add_migration(pathology_migration)
    reg.attach("pathology.build_runtime", build_runtime)
    reg.attach("pathology.synthetic_warehouse", synthetic_warehouse)
    # LT-01：领域钩子（overview 贡献键迁移至 hooks.py；build_runtime 双附件并存，
    # 新钩子键 HOOK_BUILD_RUNTIME 优先、旧键兜底——旧路径不动）。
    from plugins.domain_hooks import HOOK_BUILD_RUNTIME, HOOK_OVERVIEW,         HOOK_TARGET_SUMMARY
    from . import hooks as _hooks
    reg.attach(HOOK_OVERVIEW, _hooks.overview_contrib)
    reg.attach(HOOK_TARGET_SUMMARY, _hooks.target_summary)
    reg.attach(HOOK_BUILD_RUNTIME, build_runtime)

    from plugins.domain_hooks import HOOK_LEDGER_SEARCH as _HLS
    from plugins.domain_hooks import HOOK_LEDGER_DETAIL as _HLD
    reg.attach(_HLS, _hooks.ledger_search)
    reg.attach(_HLD, _hooks.ledger_detail)   # LT-03-R1：详情按 domain 绑定校验归属
