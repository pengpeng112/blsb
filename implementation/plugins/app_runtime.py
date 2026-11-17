"""通用插件装配中心（W11 → LT-01 修订）：manifest 文件驱动，核心不再硬编码领域。

- 每个领域插件提供 plugin.py::register(reg)，登记节点处理器、编号迁移
  与领域能力（如病理 Runtime 构建）。
- 核心保持通用概念（Task/Package/Asset/Submission/Publication）；worker 与
  core_api 通过 PluginRegistry.handler_for 解析，未知 plugin_key 无回退（R19）。
- BLYB_PLUGINS 环境变量选择启用集（逗号分隔，默认全量）；metrics-only
  进程设 BLYB_PLUGINS=metrics 即完全不加载病理模块。
- LT-01：manifest 从各插件目录 manifest.json 自动发现（discover_manifests）；
  PLUGIN_MANIFESTS 保留为派生字典（兼容旧代码/测试），key 集合不变。
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

_DOMAINS_DIR = Path(__file__).parent / "domains"


def discover_manifests(root: Path | str | None = None) -> dict[str, dict]:
    """扫描 domains/*/manifest.json → {key: manifest}。

    - 必填字段缺失 → RuntimeError（fail-closed，不带病加载）；
    - 重复 key → RuntimeError；
    - root 参数供测试注入临时目录。
    """
    base = Path(root) if root is not None else _DOMAINS_DIR
    out: dict[str, dict] = {}
    for mf in sorted(base.glob("*/manifest.json")):
        data = json.loads(mf.read_text(encoding="utf-8"))
        for k in ("key", "module", "domain", "capabilities", "description"):
            if k not in data:
                raise RuntimeError(f"manifest {mf} missing {k}")
        if data["key"] in out:
            raise RuntimeError(f"duplicate plugin key {data['key']}")
        out[data["key"]] = data
    return out


PLUGIN_MANIFESTS: dict[str, dict] = discover_manifests()


def _hook_accepts_window(fn) -> bool:
    """target_summary 钩子是否声明可选 window= 关键字（RT-03 S04 探测）。"""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):      # 非常规可调用：按不支持处理
        return False
    p = params.get("window")
    return p is not None and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)


class PluginRegistry:
    """已加载插件集合：处理器表 + 迁移 + 领域能力附件。"""

    def __init__(self):
        self.handlers: dict[tuple[str, str], object] = {}
        self.migrations: list = []
        self.enabled: list[str] = []
        self._attached: dict[str, list] = {}

    def add_handlers(self, entries: dict[tuple[str, str], object]) -> None:
        self.handlers.update(entries)

    def add_migration(self, migration) -> None:
        self.migrations.append(migration)

    def attach(self, key: str, value) -> None:
        """登记能力附件；同名多插件按追加保留全部（worker job/事件路由合并用）。"""
        self._attached.setdefault(key, []).append(value)

    def capability(self, key: str):
        vals = self._attached.get(key) or []
        return vals[-1] if vals else None

    def capability_list(self, key: str) -> list:
        return list(self._attached.get(key) or [])

    def capability_merged(self, key: str) -> dict:
        merged: dict = {}
        for v in self._attached.get(key) or []:
            if isinstance(v, dict):
                merged.update(v)
        return merged

    def has(self, plugin_key: str) -> bool:
        return plugin_key in self.enabled

    def handler_for(self, node_type: str, plugin_key: str):
        """严格查找（R19）：未注册返回 None，由 engine 报 CAPABILITY_UNSUPPORTED。"""
        return self.handlers.get((node_type, plugin_key))

    def build_runtime(self, db, tenant_id: str, **kw):
        # LT-01：经 HOOK_BUILD_RUNTIME 钩子解析（病理插件登记）；
        # 兜底旧附件键 pathology.build_runtime（不删，旧路径不动）。
        from plugins.domain_hooks import HOOK_BUILD_RUNTIME
        fn = self.capability(HOOK_BUILD_RUNTIME) \
            or self.capability("pathology.build_runtime")
        if fn is None:
            from connector_sdk import CapabilityError
            raise CapabilityError(
                "CAPABILITY_UNSUPPORTED",
                "pathology plugin not enabled in this process")
        return fn(db, tenant_id, **kw)

    def overview_contribs(self, db, tenant_id: str) -> dict:
        """聚合各插件 overview 钩子的贡献键（LT-01；核心不 import 领域）。"""
        from plugins.domain_hooks import HOOK_OVERVIEW
        merged: dict = {}
        for fn in self.capability_list(HOOK_OVERVIEW):
            merged.update(fn(db, tenant_id))
        return merged

    def target_summary(self, db, tenant_id: str, target_code: str, days: int,
                       *, window=None):
        """按登记顺序取第一个返回非 None 的 target_summary 钩子（LT-01）。

        RT-03 S04：window=(begin, end) 为可选显式 UTC 半开区间（早报自然日
        窗口）。向后兼容——仅声明 **window 关键字参数**的钩子收到它（inspect
        探测，旧钩子签名 (db, tenant_id, target_code, days) 照旧）；支持窗口
        的钩子返回 window_kind="hospital_day"，不支持的自然日窗口不可用，
        调用方（digest）须显示"仅近24h"而非把滚动数字塞进昨日栏。"""
        from plugins.domain_hooks import HOOK_TARGET_SUMMARY
        import inspect
        for fn in self.capability_list(HOOK_TARGET_SUMMARY):
            if window is not None and _hook_accepts_window(fn):
                r = fn(db, tenant_id, target_code, days, window=window)
            else:
                r = fn(db, tenant_id, target_code, days)
            if r is not None:
                return r
        return None

    def ledger_search(self, db, tenant_id: str, target_code: str, filters,
                      cursor, limit: int):
        """按登记顺序取第一个认领该 target 的台账钩子（LT-03；None=无人认领）。"""
        from plugins.domain_hooks import HOOK_LEDGER_SEARCH
        for fn in self.capability_list(HOOK_LEDGER_SEARCH):
            r = fn(db, tenant_id, target_code, filters, cursor, limit)
            if r is not None:
                return r
        return None

    def ledger_detail(self, db, tenant_id: str, target_code: str,
                      submission_id: str):
        """台账详情钩子分发（LT-03-R1；None=无插件认领，路由层走通用兜底）。

        领域认领的详情（如病理按 domain 绑定）必须由钩子实现归属校验。"""
        from plugins.domain_hooks import HOOK_LEDGER_DETAIL
        for fn in self.capability_list(HOOK_LEDGER_DETAIL):
            r = fn(db, tenant_id, target_code, submission_id)
            if r is not None:
                return r
        return None

    def target_classification(self, db, tenant_id: str, target_code: str,
                              days: int, source_instance: str | None = None):
        """分类统计钩子分发（RT-04 A3；None=无插件认领）。"""
        from plugins.domain_hooks import HOOK_TARGET_CLASSIFICATION
        for fn in self.capability_list(HOOK_TARGET_CLASSIFICATION):
            r = fn(db, tenant_id, target_code, days,
                   source_instance=source_instance)
            if r is not None:
                return r
        return None


def enabled_plugins() -> list[str]:
    env = os.environ.get("BLYB_PLUGINS", "")
    if not env:
        return list(PLUGIN_MANIFESTS)
    keys = [k.strip() for k in env.split(",") if k.strip()]
    unknown = [k for k in keys if k not in PLUGIN_MANIFESTS]
    if unknown:
        raise RuntimeError(
            f"unknown plugin(s) in BLYB_PLUGINS: {unknown}; "
            f"available: {sorted(PLUGIN_MANIFESTS)}")
    return keys


def load_registry(keys: list[str] | None = None) -> PluginRegistry:
    reg = PluginRegistry()
    # 基线通用处理器（核心概念：目标映射/校验/投递/回执/发布）——任何领域可用
    from core_handlers import (h_deliver_publish, h_deliver_push, h_receipt_wait,
                               h_target_map, h_target_validate)
    reg.add_handlers({
        ("target.map", "core.mapping"): h_target_map,
        ("target.map", "profile.pathology_internal_v1"): h_target_map,
        ("target.validate", "core.schema"): h_target_validate,
        ("deliver.push", "destination.mock_http"): h_deliver_push,
        ("receipt.wait", "destination.mock_http"): h_receipt_wait,
        ("deliver.publish", "destination.view_publication"): h_deliver_publish,
    })
    # LT-03：generic_http_json 适配器注册（真实通用推送目的地；闸门在
    # perform_push 派发处生效——见 core_handlers._destination_config /
    # assert_target_can_send_real）
    from destinations.generic_http_json import perform_generic_push
    from core_handlers import register_destination_adapter
    register_destination_adapter("destination.generic_http_json",
                                 perform_generic_push)
    reg.add_handlers({("deliver.push", "destination.generic_http_json"):
                      h_deliver_push})
    for key in (enabled_plugins() if keys is None else keys):
        manifest = PLUGIN_MANIFESTS.get(key)
        if manifest is None:
            raise RuntimeError(f"unknown plugin key: {key}")
        mod = importlib.import_module(manifest["module"])
        mod.register(reg)
        reg.enabled.append(key)
    return reg
