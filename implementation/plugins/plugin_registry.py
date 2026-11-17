"""插件装配中心（全量兼容层）。

**弃用说明（LT-09）**：新代码请使用 `app_runtime.load_registry`（显式插件集）；
本模块保留为兼容层（14 个旧引用方），不删除、不改行为。移除条件：所有调用点
迁移 + 部署环境显式设置 BLYB_PLUGINS（见 接口与设计差异.md LT-09 节）。

W11 拆分后：通用处理器在 core_handlers，病理领域在
domains.pathology.{handlers,plugin}，metrics 领域在 domains.metrics.plugin，
manifest 加载在 app_runtime。本模块加载全量插件（pathology+metrics）并
保持既有导入面（build_runtime/NODE_HANDLERS/MIGRATIONS/handler_for/...）。
metrics-only 进程请使用 app_runtime.load_registry(["metrics"])，
不要 import 本模块（那会加载病理插件）。
"""
from __future__ import annotations

from connector_sdk import CapabilityError  # noqa: F401  （历史导入面）

from core_handlers import (HandlerCtx, _store_receipt, enqueue,  # noqa: F401
                           apply_business_confirmation,  # noqa: F401
                           h_deliver_publish, h_deliver_push, h_receipt_wait,
                           h_target_map, h_target_validate, production_allowed,
                           _package_revision, _prev_output)
from domains.pathology.handlers import (WAREHOUSE_SINGLETON,  # noqa: F401
                                        _published_pack, _runtime_for,
                                        build_runtime, h_pathology_completeness,
                                        h_pathology_freeze, h_pathology_quality,
                                        h_pathology_resolve, synthetic_warehouse)
from domains.pathology.models import migration as pathology_migration  # noqa: F401
from domains.metrics.plugin import (h_metrics_freeze, h_metrics_import,  # noqa: F401
                                    h_metrics_map)
import app_runtime

# 全量装配（pathology + metrics；含 app_runtime 的基线通用处理器）
_registry = app_runtime.load_registry(["pathology", "metrics"])

NODE_HANDLERS: dict[tuple[str, str], object] = dict(_registry.handlers)

MIGRATIONS = list(_registry.migrations)


def handler_for(node_type: str, plugin_key: str):
    """严格注册查找（R19）：未知 plugin_key 不回退到同 node_type 的其他 handler，
    由调用方报 CAPABILITY_UNSUPPORTED——未知发送插件绝不能跑成 mock。"""
    return NODE_HANDLERS.get((node_type, plugin_key))


def default_registry() -> app_runtime.PluginRegistry:
    """按 BLYB_PLUGINS 环境变量装配（metrics-only 进程的入口）。"""
    return app_runtime.load_registry()


def shared_registry() -> app_runtime.PluginRegistry:
    """RT-02 单轨装配：返回本模块已装配的同一 PluginRegistry 实例。

    未设 BLYB_PLUGINS 的进程（core_api/worker）以前拿不到任何 registry 对象
    （_loaded_registry=None，ops_routes 丢失全部领域任务类型）。现在 app.py /
    engine.py 未设 BLYB_PLUGINS 时复用本实例——旧兼容层导出的处理器表与本
    实例是同一套，不产生两套分裂表。"""
    return _registry
