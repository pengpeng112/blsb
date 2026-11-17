# ADR-DEV-05：采集端 Agent 以 PyInstaller 打包为免安装 exe（含运行环境）

日期：2026-09-30　状态：已接受（Win10/11 已实测；Win7 材料就绪、未执行）

## 背景
扫描工作站（Win7/10/11，医院普遍不装 Python）需要免安装运行采集 Agent。
Agent 依赖面刻意保持极小：httpx + 标准库 sqlite3（不依赖 SQLAlchemy/FastAPI），
适合冻结打包。

## 决策
- 打包工具 **PyInstaller**（6.21，onefile，console）。构建脚本
  `deploy/windows/build-agent-exe.ps1`；产物 `deploy/windows/dist/BLYB-EdgeAgent.exe`
  （≈32MB，含解释器与依赖）与 `BLYB-ScanSimulator.exe`（现场无扫描仪时造测试数据）。
- **冻结态数据路径**：exe 同目录放 `agent-config.json`、`scanner-output/`、
  `agent-queue.sqlite3`（`run.py::base_dir()` 按 `sys.frozen` 分流；开发态仍走仓库 `var/`）。
  exe 所在目录须可写（不要放 Program Files）。
- 首次运行自动注册（`BLYB_ENROLL_TOKEN`），`--device-code` 区分多工作站；
  注册返回的令牌写回配置文件。
- **Win7**：Python≥3.9 不支持 Win7 → 需独立 Python 3.8 工具链按
  `deploy/windows/win7/README.md` 构建（依赖清单 requirements-agent-win7.txt；
  Agent 代码已按 3.8 兼容：无 3.9+ API，注解走 future import）。本机无 3.8/Win7，
  **未执行**，属阶段F验证项。
- 服务化沿用 `install-agent-service.ps1`（任务计划指向 exe 即可）。

## 实测证据（Win10 19045，2026-09-30）
模拟器 exe 生成扫描 → Agent exe 首跑（写配置/自动注册 SCAN-EXE-01/发现1/传输1，退出码0）
→ 后端新增采集 FILES_COMPLETE、资产+2、设备在线 → 二次运行幂等（发现0，退出码0）。

## 后果与风险
- 正：医院工作站零依赖部署；升级=替换 exe（配置/队列在外部，不受影响）。
- 负：onefile 启动需自解压（~1-2s）；未签名 exe 可能被杀软/SmartScreen 拦——
  生产建议 onedir+代码签名（构建脚本留 `-OneDir` 开关）。
- TLS：https 场景需院内 CA 信任链可用（httpx/certifi）；当前演示为 http 内网。
