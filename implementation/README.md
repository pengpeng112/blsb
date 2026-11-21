# 医院数据归集与上报平台（实现目录）

> 设计基线：`../设计交付v0.4/` · 当前状态：[docs/ai/STATE.md](../docs/ai/STATE.md) · 根目录统一入口：[README.md](../README.md) / [project.ps1](../project.ps1)

本目录为唯一业务源码根，支持 **5 大上报业务**（码上放心药品追溯、传染病前置软件、山东医保电子病历 23 交易、医保病理云索引、济南医保影像云 16 数据集）与 HTTP JSON / 视图发布 / SOAP 1.1 & 1.2 XML 协议。

## 一、一键启动（优先从仓库根目录用 `project.ps1`）

```powershell
# 推荐：仓库根目录执行（离线复用现有环境）
..\project.ps1 -Action Dev        # core(8000)/storage(8001)/exchange(8002)/mock(8090) + worker
..\project.ps1 -Action Demo       # 同上 + 3 片+1 重扫与 Agent 演示
..\project.ps1 -Action Showcase   # 灌入 5 业务高仿真演示数据
..\project.ps1 -Action Health     # 只读探测 8000/8001/8002/8090 /health
```

本目录直接启动方式（保留兼容）：

```bash
python run_dev.py            # 迁移+种子+启动全部本地服务
python run_dev.py --demo     # 额外生成演示扫描并跑 Agent 一轮
```

本地开发令牌（仅本地，生产加固模式禁用）：`dev-admin-token` / `dev-configer-token` / `dev-reviewer-token` / `dev-operator-token` / `dev-auditor-token` / `dev-agent-token` / `dev-collector-token` / 注册令牌 `dev-enroll-token`。

前端控制台（`apps/admin_web/`，离线使用现有 `node_modules`）：

```bash
cd apps/admin_web
npm run dev        # http://localhost:5173
npm run build      # vue-tsc + vite build -> dist/
npm test           # node --test 前端自动化与 UX 守门
```

## 二、目录结构

```text
apps/      core_api(8000) storage_service(8001) exchange_gateway(8002)
           edge_agent worker admin_web(Vue3)
packages/  core_db contracts workflow_core connector_sdk mapping_engine storage_backend
plugins/   domains/{drug_trace,infectious,medins_emr,pathology,medins_imaging,metrics}
           alerting external_ledger formats/{xml_payload} destinations/{mock_http,view_publication,soap_xml}
profiles/  各业务 Profile 契约与样例
tests/     unit contract integration fault e2e performance functional
deploy/    inf_pusher inf_watchdog intranet dmz linux oracle offline windows compose
docs/      四类实施文档 / adr / runbooks / audit/2026-10-01（43 项探针）
```

## 三、常用验证与打包命令

```powershell
# 后端回归与探针（在 implementation/ 目录执行）
python -m pytest tests -q -ra                                    # 后端全量回归
python -m pytest docs/audit/2026-10-01/test_review_probes.py -q  # 43 项复核探针
python deploy/inf_pusher/selftest.py                             # 传染病推送器 69 项自测
python deploy/inf_watchdog/selftest.py                           # 传染病守护 111 项自测

# 采集端打包为免安装 EXE（Win10/11 已实测；Win7 见 deploy/windows/win7/README.md）
powershell -File deploy\windows\build-agent-exe.ps1              # dist/BLYB-EdgeAgent.exe + BLYB-ScanSimulator.exe
powershell -File deploy\windows\build-agent-exe.ps1 -OneDir      # 杀软敏感环境目录版
```

## 四、核心环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BLYB_DATABASE_URL` | `packages/var/blyb.sqlite3` | 核心库；生产用 `postgresql+psycopg://...` |
| `BLYB_EXCHANGE_DB` | `var/exchange.sqlite3` | 交换发布库 |
| `BLYB_MOCK_PLATFORM_URL` | `http://127.0.0.1:8090` | 本地 Mock 平台地址 |
| `BLYB_DISABLE_DEV_TOKENS` | 空 | 设为 `1` 关闭本地 dev 令牌（内网生产必需） |
| `BLYB_BOOTSTRAP_ADMIN_TOKEN` | 空 | 生产引导管理员令牌（≥24 位随机值） |
| `BLYB_ENROLL_TOKEN` | `dev-enroll-token` | Agent 注册一次性令牌 |

## 五、四类实施文档

- [实施状态矩阵](docs/实施状态矩阵.md) · [外部阻断清单](docs/外部阻断清单.md) · [测试执行记录](docs/测试执行记录.md) · [接口与设计差异](docs/接口与设计差异.md) · [ADR](docs/adr/README.md) · [Runbooks](docs/runbooks/README.md)
