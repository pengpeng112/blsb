# 医院内网部署（SQLite 单机）

这是当前可以落地的部署形态：一台 Windows 服务器，数据在本机 SQLite，不连接外网。
它跑文件归集、Mock 交换和控制台。真实 HIS/PIS/医保仍未接通。

## 环境

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 或 Windows Server。Win7 未验证 |
| Python | **CPython 3.14 64 位**。离线 wheel 里的 `pydantic_core`、`sqlalchemy` 是 `cp314-win_amd64`，3.12/3.13 不能装这套 wheel |
| Node | 目标机不需要。控制台用已构建的 `apps/admin_web/dist/` |
| 磁盘 | 系统盘之外再留对象卷空间。50GiB 压力测试未做 |
| 端口 | 本机 8000、8001、8002、8090 必须空闲。脚本发现占用就退出，不会结束别人的进程 |
| 数据库 | 当前启动脚本使用 SQLite。PostgreSQL 迁移材料在，但没有真实 PG 实测，compose 也没有做过清洁安装 |

发行目录至少要带上：`apps/`、`packages/`、`plugins/`、`profiles/`、`run_dev.py`、`deploy/`、`apps/admin_web/dist/`。

## 首次安装依赖

只在目标机第一次执行。已装好相同版本依赖时跳过。不要访问 PyPI。

```powershell
cd <implementation 目录>
python -m pip install --no-index --find-links deploy\offline\dist\wheelhouse `
    -r deploy\offline\dist\requirements.lock.txt
```

材料说明见 `deploy/offline/README.md`。清洁机器的完整安装演练还没有做过。

## 启动

在当前 PowerShell 窗口设置环境变量。不要把令牌写进脚本、仓库或日志。

```powershell
cd <implementation 目录>
$env:BLYB_DISABLE_DEV_TOKENS = "1"
$env:BLYB_BOOTSTRAP_ADMIN_TOKEN = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | ForEach-Object {[char]$_})
# 其他机器要访问时再设置。只本机用可以不设，默认绑定 127.0.0.1
$env:BLYB_BIND_HOST = "0.0.0.0"
# 扫描工作站注册用。不设时仍是 dev-enroll-token，生产要换成随机值
$env:BLYB_ENROLL_TOKEN = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | ForEach-Object {[char]$_})

powershell -ExecutionPolicy Bypass -File deploy\intranet\start.ps1
```

脚本会拒绝两种情况：没关 dev 令牌或引导令牌短于 24 字符；8000/8001/8002/8090 已被占用。
实验室才可以使用 `start.ps1 -AllowDevTokens`。

启动成功后进程是隐藏的，关掉当前窗口不会停服务。日志在：

- `var\intranet\run_dev.out.log`
- `var\intranet\run_dev.err.log`
- 进程号：`var\intranet\core.pid`

数据目录：

| 文件 | 路径 |
|---|---|
| 核心库 | `var\intranet\core.sqlite3` |
| 交换库 | `var\intranet\exchange.sqlite3` |
| 对象卷 | `var\intranet\volume1` |

对象卷由 `BLYB_VOLUME_ROOT` 决定，启动脚本会设成上一行的目录。
如果旧库里的 `vol1` 还指着 `var\volume1`，并且还没有任何对象位置，下一次成功启动会把根改到 `var\intranet\volume1`。
已经有对象位置时，启动会失败并保持原根，需要按 `docs/runbooks/备份与恢复.md` 做新根恢复，不能直接改路径。

## 检查

四个地址都应返回 200：

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8001/health
curl.exe http://127.0.0.1:8002/health
curl.exe -o NUL -w "%{http_code}" http://127.0.0.1:8000/ui/
```

控制台：`http://127.0.0.1:8000/ui/`。内网其他机器用 `http://<服务器IP>:8000/ui/`，并且启动前设置了 `BLYB_BIND_HOST=0.0.0.0`。
登录前把页面里的 `blyb.core` 设为 `http://<服务器IP>:8000`，`blyb.storage` 设为 `http://<服务器IP>:8001`。

登录接口：

```text
POST /api/v1/auth/login
{"token": "<BLYB_BOOTSTRAP_ADMIN_TOKEN>"}
```

成功后角色是 `platform_admin`。`dev-admin-token` 在加固模式下应返回 401。
换引导令牌时，用新值再执行一次 `start.ps1`（先 stop）。旧令牌立即失效。令牌只留在环境变量里。

## 停止

```powershell
powershell -ExecutionPolicy Bypass -File deploy\intranet\stop.ps1
```

停止会核对进程已经退出。日志文件保留。

## 扫描工作站

```text
POST /api/v1/agents/enroll
{"enroll_token": "<BLYB_ENROLL_TOKEN>", "tenant_code": "first-hospital",
 "source_system_code": "scanner-simulator", "device_code": "SCAN-WS-01"}
```

Agent 的 `core_url` 指 `http://<服务器IP>:8000`，`storage_url` 指 `http://<服务器IP>:8001`。
返回的 agent 令牌只出现一次。同设备再注册会轮换旧令牌。

## 这次部署不包括

- 真实 PIS、HIS、迪英加扫描仪、医保平台（B01—B09 仍是 OPEN）
- PostgreSQL 生产库和 Docker 清洁安装
- Windows 服务管理器里的 Agent 服务（只有脚本，未在 SCM 验证）
- Win7 Agent、50GiB 压测
- 画布拖拽（现有页面仍是受限表单编辑）

Mock 目的地会标明自己不是真实医保平台。不要把本机演示通过说成医院验收。
