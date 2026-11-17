# 当前项目状态

核对日期：2026-10-16。基线：**v0.4**。业务源码：`implementation/`。
这是共享的当前摘要。详细证据仍在实施文档和 `implementation/var/` 的 log/XML。

## 已执行到哪里

| 工作 | 当前证据状态 |
|---|---|
| 第1—3轮与真实栈功能测试 | 既有记录。功能测试后回归 191/6，43 项复核通过 |
| 第4轮本地收尾（2026-10-02） | 216 passed / 6 skipped（var/round4-final-regression2.log）；T12 拖拽未做 |
| 2026-10-05 部署修订 | 内网卷隔离+令牌强制+端口退出（T14 两项 2 passed） |
| 2026-10-16 Linux 生产部署修订（REPORTING-LINUX-DEPLOY-20261016） | **Git 基线建立**（b7fda30 起；快照清单 snapshots/baseline-20261016，995文件）；§5 缺口全部落地（bootstrap/enroll fail-closed/心跳/同源前端/compose+nginx/脚本）；**隔离验证 23/23 PASS**；**全量回归 229 passed / 6 skipped / 0 failed**（var/deploy-r2-full-regression.*）；发行 rel-20261016T105938Z（118文件哈希） |

**最近业务终验是 2026-10-16 的 229/6/0**（包含并超过 2026-10-02 的 216/6 基线）。
期间修复四个真实缺陷：PG迁移建表拓扑序、compose 未把注册令牌传给 core（默认密钥漏洞）、
postgres initdb 健康竞态、bootstrap 测试进程污染（详见 docs/…/测试执行记录.md）。

## 可以部署的形态

- Windows 单机（SQLite）：`implementation/deploy/intranet/start.ps1`（旧路径，仍有效）。
- **Linux Compose 生产形态（新增，已隔离验证）**：发行包构建
  `implementation/deploy/release/build_release.py`；目标机安装
  `implementation/deploy/linux/install.sh`（含离线 Docker 材料 docker-offline/）；
  唯一入口 nginx TLS 443，后端 8000/8001/8002 不发布宿主机端口。
- <prod-host> 部署进行中（会话 REPORTING-LINUX-DEPLOY-20261016）。

## 尚未满足的条件

- 真实 HIS/PIS/扫描仪/医保（B01—B09）仍 OPEN；码上放心模块（P5）未开发；DMZ 转发未部署。
- <prod-host> 上 TLS 用临时自签证书（院方证书未提供，待替换）。
- Docker 清洁安装到 <prod-host> 首次执行中；院方异机备份策略未定。
- T12 受限画布拖拽未做。

## 维护方法

新的已验证结果写到本文件，并链到 session 和 log/XML。Git 已建立（main 分支）；
凭据/运行库/患者文件不入库（.gitignore/.gitattributes 已约束）。
