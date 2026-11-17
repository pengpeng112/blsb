# 医院数据归集与上报平台（实现）

版本 0.4.0（本地实现）。对应设计基线：`../设计交付v0.4/`（v0.4 为唯一开发基线）。
本目录为可运行实现：病理为首个业务插件，通用内核 + 插件 + 双模式交换 + Vue3 控制台。

> 当前状态见 [共享STATE](../docs/ai/STATE.md)：第3轮及功能测试已有记录，
> 第4轮仍有API/UI/部署缺口，PG/容器等形态未完整验收。真实医院/设备/医保验收
> 未执行（阶段F，见 `docs/外部阻断清单.md` B01—B09），不得宣称生产病理医保上传完成。

根目录通用入口：[README](../README.md)、[project.ps1](../project.ps1)、[AI接手](../docs/ai/START_HERE.md)。
分类查阅：[实施文档入口](docs/README.md)。旧应用入口与数据位置保持有效。

## 一键启动（阶段A退出条件）

推荐从根目录执行 `project.ps1 -Action Dev` 或 `Demo`；现有环境可直接使用，无自动安装。
下面保留原始手工路径；离线任务不要执行联网安装步骤，按现有依赖和任务约束运行。

```bash
cd implementation
pip install -r requirements.txt
python run_dev.py            # 迁移+种子+启动 core(8000)/storage(8001)/exchange(8002)/mock(8090)+worker
python run_dev.py --demo     # 额外生成 3片+1重扫 演示并跑Agent一轮
```

开发令牌（仅本地）：`dev-admin-token`（平台管理员）/ `dev-configer-token` /
`dev-reviewer-token` / `dev-operator-token` / `dev-auditor-token` /
`dev-agent-token`（Agent）/ `dev-collector-token`（交换采集器）/ 注册令牌 `dev-enroll-token`。

控制台：

```bash
cd apps/admin_web
npm install
npm run dev        # http://localhost:5173 （vite代理 /core /storage /exchange）
npm run build      # 产物 dist/（vue-tsc 类型检查 + vite 构建）
```

## 目录

```
apps/      core_api(8000) storage_service(8001) exchange_gateway(8002)
           edge_agent(采集端+扫描模拟器) worker(任务/DAG引擎) admin_web(Vue3)
packages/  core_db contracts workflow_core connector_sdk mapping_engine storage_backend
plugins/   domains/{pathology,metrics} sources/db_view(+fixtures)
           destinations/{mock_http,view_publication} plugin_registry(装配)
profiles/  pathology_internal_v1 jinan_imaging_v2_draft（Profile占位）
tests/     unit contract integration fault e2e performance
deploy/    offline windows compose
docs/      实施状态矩阵 / 外部阻断清单 / 测试执行记录 / 接口与设计差异 / adr / runbooks
```

## 常用命令

```bash
python -m pytest tests -q                 # 全量测试（~4分钟，含600MB性能）
python -m pytest tests/integration/test_first_hospital_cases.py -q   # F01—F20
python -m apps.edge_agent.scanner_simulator --single 900001          # 追加一次扫描
python -m apps.edge_agent.run --once     # Agent 单轮（发现→传输→心跳）
python -m apps.worker.engine             # 独立worker进程（生产形态）
BLYB_RUN_50GB=1 python -m pytest tests/performance -q                # 50GB压力（需磁盘窗口）
BLYB_PIS_SQLSERVER_DSN=... python -m pytest tests/integration/test_real_drivers.py -q  # 真库验证
```

### 采集端打包为免安装 exe（含运行环境）

```powershell
powershell -File deploy\windowsuild-agent-exe.ps1            # dist/BLYB-EdgeAgent.exe + BLYB-ScanSimulator.exe
powershell -File deploy\windowsuild-agent-exe.ps1 -OneDir   # 杀软敏感环境用目录版
```
exe 同目录运行：`agent-config.json`（首跑生成）、`scanner-output/`、`agent-queue.sqlite3`；
多工作站用 `--device-code` 区分。Win10/11 已实测；Win7 见 `deploy/windows/win7/README.md`（需独立 Python 3.8，未执行）。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BLYB_DATABASE_URL` | `packages/var/blyb.sqlite3` | 核心库；生产 `postgresql+psycopg://...` |
| `BLYB_EXCHANGE_DB` | `var/exchange.sqlite3` | 交换发布库（独立） |
| `BLYB_MOCK_PLATFORM_URL` | `http://127.0.0.1:8090` | Mock医保平台地址（非真实） |
| `BLYB_ENROLL_TOKEN` | `dev-enroll-token` | Agent 注册一次性令牌 |
| `BLYB_PIS_POLL_ENABLED` | `0` | PIS CreateTime轮询（仅新增发现） |
| `BLYB_PIS_SQLSERVER_DSN` / `BLYB_ORACLE_DSN` | 空 | 真实驱动（B01授权后） |

## 文档

- `docs/实施状态矩阵.md`：逐项代码路径/状态/测试映射
- `docs/外部阻断清单.md`：B01—B09 与解锁路径
- `docs/测试执行记录.md`：命令、退出码、实测数据、未执行项
- `docs/接口与设计差异.md`：与设计交付v0.4的差异与已知限制
- `docs/adr/`：实现期技术决策
- `docs/runbooks/`：启动/备份恢复/离线发行/故障处置
