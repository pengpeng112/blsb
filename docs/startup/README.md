# 启动、测试和部署入口分类

推荐从根目录调用 [project.ps1](../../project.ps1)。包装器固定工作目录、委托现有入口并透传退出码，不安装依赖。
例：`powershell -NoProfile -File F:\python\blyb\project.ps1 -Action Dev`。

医院各系统数据库查询统一使用 [sjzc 数据库入口](DATABASE_ACCESS.md)：
先查平台元数据，满足当前授权与源技能门禁后再限量只读核验；不读取凭据内容。

## 先按具体上报项目定位材料

从[数据上报总索引](../../数据上报/README.md)选择项目，再读取该业务目录里的README：[码上放心](../../数据上报/1、码上放心平台/README.md)、[医保电子病历](../../数据上报/2、医保电子病历上报/README.md)、[病理](../../数据上报/3、病理上报/README.md)。

**每个上报项目都必须在 `数据上报/<编号、业务名>/README.md` 建立并维护目录说明。** 按[模板](../../数据上报/上报项目目录说明模板.md)写清原件/接口及版本、项目代码路径、SQL/视图及映射、服务器与调用方向、凭据受限存放位置、启动/部署/回退、实测证据和未完成项。
新增项目或修改相关接口、路径、部署、证据时一并更新业务README和总索引；不把密码或患者资料写进README，不复制一套源码或状态。具体业务原件/代码/部署资料位置由这些说明给出，不能从别的上报项目猜。
该要求已同步根AGENTS、AI_START和共享接手入口；资料索引不意味着自动获得生产连库或发送权限。

## 本地开发和演示

| Action | 原入口/工作目录 | 数据与服务 |
|---|---|---|
| Dev | implementation内 `python run_dev.py` | 迁移+合成种子；core8000/UI、storage8001、exchange8002、mock8090、一个worker |
| Demo | implementation内 `python run_dev.py --demo` | 同上，加3片+1重扫与Agent演示 |
| Metrics | implementation内 `python apps/run_metrics_only.py` | 独立metrics-only，默认core8010，隔离根var/metrics-only；按原入口声明的能力使用 |
| Agent | implementation内 `python -m apps.edge_agent.run --once` | Agent单轮；会读取/生成agent配置并登记/上传合成来源，只用于已配置的本地演示 |

默认核心库在 `implementation/packages/var/`，交换库/卷/日志在 `implementation/var/`；包装器保留这些路径。
Dev/Demo拒绝已占用的四个端口，不自动终止原进程；若继承非SQLite数据库URL，拒绝对其写合成种子。
前台Ctrl+C停止本次栈。旧脚本和已在运行的服务不由包装器接管。

## 验证/维护

```powershell
.\project.ps1 -Action Test
.\project.ps1 -Action Test -TestPath tests/integration/test_w10_w11.py
.\project.ps1 -Action Probes
.\project.ps1 -Action Build
.\project.ps1 -Action Docs
.\project.ps1 -Action Status
.\project.ps1 -Action Context
.\project.ps1 -Action Dev -DryRun
```

TestPath仅允许implementation/tests范围；Probes有独立入口。Build使用已安装node_modules，不能因构建缺依赖自动联网。
Docs检查活动文档相对链接与来源/归档hash；目录新增/归档后执行 `python scripts/project_docs.py inventory` 更新目录。
Status只读取本地协作登记；Context只导出允许的共享文档。

正式回归会按环境门控跳过功能/真驱动/PG/50GiB等测试，不能把skip当通过。
真实栈旧入口 `implementation/tests/functional/run_live.sh` 会终止固定端口进程并清测试目录，使用前核实全部为独立测试数据和本任务拥有的服务。
它不属于默认Test入口；本次整理未运行它、未终止任何已有服务。

## 编号迁移和单服务部署

迁移使用显式目标，避免在错误目录生成另一个库：

```powershell
$env:BLYB_DATABASE_URL = 'sqlite:///F:/python/blyb/implementation/var/example/core.sqlite3'
.\project.ps1 -Action Migrate
```

示例父目录须先存在；实际目标由当前任务配置。无需迁移时不要运行。
业务数据库配置不写入公共交接文件，命令/日志不回显真实连接秘密。

单服务的原入口在implementation目录执行：

```powershell
$env:BLYB_FACTORY = '1'
$env:BLYB_STORAGE_EMBEDDED_WORKER = '0'
python -m apps.migrate
python -m uvicorn apps.core_api.app:get_app --factory --host 127.0.0.1 --port 8000
# 另终端：storage/exchange对应get_app --factory，分别8001/8002
# worker：python -m apps.worker.engine
```

迁移目标、对象卷、exchange路径、secret_ref与权限必须由部署配置显式提供。
`run_dev.py`是合成开发栈。医院内网单机部署用 `implementation/deploy/intranet/start.ps1`，说明在 [内网部署](../../implementation/deploy/intranet/README.md)。不要用 Dev/Demo 启动生产库。

## 其他形态及证据边界

| 形态 | 文件 | 当前验证边界 |
|---|---|---|
| 容器/PG | implementation/Dockerfile、deploy/compose/docker-compose.yml | 有材料，当前记录仍缺PG实测/清洁容器安装 |
| Windows计划任务 | deploy/windows/install-agent-service.ps1 | 实际是计划任务，不称SCM服务 |
| Windows SCM | deploy/windows/install-agent-scm.ps1、apps/edge_agent/win_service.py | 框架，现场/pywin32验证待完成 |
| EXE打包 | deploy/windows/build-agent-exe.ps1、win7/README | 原有Win10/11证据与Win7未执行分列 |
| 离线发行 | deploy/offline/、[runbook](../../implementation/docs/runbooks/离线发行材料.md) | 文件/hash存在不等于隔离网完整安装通过 |
| 内网 SQLite 单机 | [intranet 启动](../../implementation/deploy/intranet/README.md) | 2026-10-02 旧脚本实跑过健康检查。2026-10-05 起启动脚本会拒绝空令牌和占用端口，对象卷改到 `var/intranet/volume1`。本机 8000 被其他程序占用，新脚本的完整拉起尚未在本会话复跑 |
| 新根恢复 | [备份恢复](../../implementation/docs/runbooks/备份与恢复.md) | 自动化新根恢复测试已有；50GiB 与清洁机器演练未做 |

2026-10-02 对包装器做了解析和各 Action DryRun，没有改开发库，也没有做生产验收。
2026-10-05 的部署修订见 [内网部署](../../implementation/deploy/intranet/README.md) 和 [HANDOFF](../ai/HANDOFF.md)。
