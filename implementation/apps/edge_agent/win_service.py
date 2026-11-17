"""Windows SCM 服务主体（W13 框架，未在本机验证）。

需要 pywin32；未安装时 import 明确失败（不静默降级为计划任务冒充）。
本模块只做服务生命周期包装：启动 AgentService.run_forever；恢复策略
（崩溃自动重启）由 SCM failure actions 配置（见 install-agent-scm.ps1）。
"""
from __future__ import annotations

import logging

log = logging.getLogger("blyb.win_service")

try:
    import servicemanager  # noqa: F401
    import win32eventutil  # type: ignore[import-not-found]
    import win32service
    import win32serviceutil  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - 环境相关
    raise ImportError(
        "pywin32 未安装：SCM 服务形态不可用。备选形态为计划任务"
        "（deploy/windows/install-agent-service.ps1，已如实标注非SCM服务）。"
    ) from exc


class BLYBAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "BLYBEdgeAgent"
    _svc_display_name_ = "BLYB Edge Agent"
    _svc_description_ = ("医院数据归集 Edge Agent（SCM服务形态；"
                         "恢复策略由服务failure actions配置）")

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32eventutil.CreateEvent(None, 0, 0, None)
        self._worker = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32eventutil.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ""))
        from .run import main as agent_main  # 复用命令行入口的装配
        import threading
        self._worker = threading.Thread(target=agent_main, daemon=True)
        self._worker.start()
        win32eventutil.WaitForSingleObject(self.stop_event, win32eventutil.INFINITE)


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(BLYBAgentService)
