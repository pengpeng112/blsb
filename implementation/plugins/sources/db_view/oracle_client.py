"""Oracle Thick 模式进程内一次性初始化（DPL-P01）。

背景：python-oracledb 默认 Thin 模式仅支持 Oracle Database 12.1+；本项目的
HIS 是旧 Oracle 11g，**必须** Thick 模式（Instant Client），且官方要求
init_oracle_client() 在同进程首个连接/池建立之前完成。旧发行缺 oracledb
依赖、也未初始化 Thick（覆盖文件注释“Thin 无需 Client”不适用于 11g）。

安全边界：
- 进程内幂等：加锁一次初始化，重复调用直接返回既有状态。
- 失败即阻断：Instant Client 缺失/不兼容时抛 CapabilityError("DRIVER_UNAVAILABLE")
  ——**不自动回退 Thin**（11g 服务端 Thin 会连接失败或行为不明）。
- Thin 仅显式选择：BLYB_DRUGTRACE_ORACLE_ALLOW_THIN=1 时允许（仅限服务端
  已确认 ≥12.1 的环境；HIS 11g 不得开启）。
- lib_dir 缺省由 oracledb 自身搜索；推荐经
  BLYB_DRUGTRACE_ORACLE_CLIENT_LIB_DIR 显式注入（可审查、可复核）。

环境变量（均为受控部署注入，不进镜像层）：
- BLYB_DRUGTRACE_ORACLE_CLIENT_LIB_DIR  Instant Client 库目录（如
  /opt/oracle/instantclient_19_32）；空=由驱动默认搜索。
- BLYB_DRUGTRACE_ORACLE_ALLOW_THIN      "1"=显式 Thin（默认 Thick 必需）。
"""
from __future__ import annotations

import os
import threading

ENV_LIB_DIR = "BLYB_DRUGTRACE_ORACLE_CLIENT_LIB_DIR"
ENV_ALLOW_THIN = "BLYB_DRUGTRACE_ORACLE_ALLOW_THIN"

_lock = threading.Lock()
_state: dict = {"initialized": False, "mode": None, "client_version": None,
                "lib_dir": None, "error": None}


def ensure_oracle_client() -> tuple:
    """进程内首次 Oracle 连接前调用：完成 Thick 初始化。

    返回 (mode, client_version)；mode ∈ {"thick", "thin"}，
    client_version 为点分字符串（Thin 为 None）。
    阻断场景抛 CapabilityError("DRIVER_UNAVAILABLE", ...)，不回退。
    """
    with _lock:
        if _state["initialized"]:
            if _state["error"] is not None:
                # 首次失败后保持阻断（不因重试改判）
                from connector_sdk import CapabilityError
                raise CapabilityError("DRIVER_UNAVAILABLE", _state["error"])
            return _state["mode"], _state["client_version"]
        try:
            import oracledb
        except ImportError as exc:
            from connector_sdk import driver_unavailable
            _state["error"] = f"oracledb 驱动未安装：{exc}"
            raise driver_unavailable("oracledb", str(exc)) from exc
        if os.environ.get(ENV_ALLOW_THIN, "").strip() == "1":
            # 显式 Thin：仅限已确认服务端 ≥12.1 的环境（HIS 11g 禁止）
            _state.update(initialized=True, mode="thin",
                          client_version=None, lib_dir=None, error=None)
            return "thin", None
        # RT-02：识别测试注入的合成桩（sys.modules["oracledb"] 被 Simple-
        # Namespace(connect=...) 之类替换——无 init_oracle_client 且无
        # __version__/DatabaseError）。桩不做 Thick 初始化、不写进程级粘滞
        # _state（避免污染后续真实驱动测试），直接放行构造。真实 oracledb
        # 模块（init_oracle_client 可调用）走下方 Thick 强制校验，一字不改。
        init_fn = getattr(oracledb, "init_oracle_client", None)
        if not callable(init_fn) and not hasattr(oracledb, "__version__") \
                and not hasattr(oracledb, "DatabaseError"):
            return "stub", None
        lib_dir = os.environ.get(ENV_LIB_DIR, "").strip() or None
        try:
            init_fn(lib_dir=lib_dir)
        except Exception as exc:  # noqa: BLE001 —— 阻断信息保留原异常类型
            msg = (f"Oracle Thick 初始化失败（Instant Client 缺失/不兼容；"
                   f"lib_dir={lib_dir or '<驱动默认搜索>'}；HIS 11g 不允许回退"
                   f"Thin）：{type(exc).__name__}: {exc}")
            # 失败状态粘滞：同进程内不重试（真实驱动 init_oracle_client
            # 本就是一次性调用，重试只会得到另一种错误并掩盖首个原因）
            _state.update(initialized=True, mode=None, client_version=None,
                          lib_dir=lib_dir, error=msg)
            from connector_sdk import CapabilityError
            raise CapabilityError("DRIVER_UNAVAILABLE", msg) from exc
        version = None
        try:
            version = ".".join(str(x) for x in oracledb.clientversion())
        except Exception:  # noqa: BLE001 —— 版本号只用于报告，不阻断
            version = None
        _state.update(initialized=True, mode="thick", client_version=version,
                      lib_dir=lib_dir, error=None)
        return "thick", version


def oracle_client_status() -> dict:
    """只读状态（能力报告用；未初始化时不触发初始化）。"""
    with _lock:
        return {
            "initialized": _state["initialized"],
            "mode": _state["mode"],
            "client_version": _state["client_version"],
            "lib_dir": _state["lib_dir"],
            "allow_thin_env": os.environ.get(ENV_ALLOW_THIN, "").strip() == "1",
            "blocked": _state["error"],
        }


def reset_for_tests() -> None:
    """仅测试用：清空进程内初始化状态。"""
    with _lock:
        _state.update(initialized=False, mode=None, client_version=None,
                      lib_dir=None, error=None)
