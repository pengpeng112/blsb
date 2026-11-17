# 基础部署复核与对接缺口

复核人：Codex / 模型 unknown；2026-10-16，主要现场证据时间 22:29—22:34（北京时间）。源码基线 Git `2d46bb8`；当前发行 `20261016T114735Z`。本报告区分现场实测、源码判断和既有测试，不重复实施部署。

## 1. 已完成及可复用部分

| 项目 | 证据与结论 |
|---|---|
| <prod-host> 基础平台 | 经 <jump-host> 普通二跳 SSH 登录成功；`/opt/blyb/current` 指向 `/opt/blyb/releases/20261016T114735Z`，发行 SHA256SUMS 校验退出 0 |
| 容器状态 | db/core/storage/exchange/worker/nginx 六个 healthy；migrate/bootstrap 正常退出 0；六服务 restart=unless-stopped，观察时重启次数均 0 |
| 端口隔离 | <prod-host> 宿主监听 22、443；未监听 8000/8001/8002/5432，compose 后端无宿主直映 |
| 数据与配置 | 数据 `<prod-path>`；`/etc/blyb/runtime.env`、TLS 私钥均 root:root 600；PG 已登记迁移 001—008 |
| 最小初始化 | 租户 1；connector、destination、submission、data_package、job 均 0。基础设施已部署，业务未配置 |
| worker | 心跳文件存在，采样时约 0.11 秒；健康结果不能证明尚未配置的业务链路可用 |
| HIS 网络 | 从 <prod-host> 到 <his-host>:1521 TCP 成功；没有数据库认证或业务 SQL |
| 管理链路 | <jump-host>:<ssh-port> 可用，经跳板到 <prod-host>:22 成功；本机直连 <prod-host>:22 曾短暂拒绝，不能把可用二跳管理链路写成全部不通 |
| 备份产物 | `<prod-path>/backups/20261016T114711Z` 有 PG dump、exchange SQLite、配置归档及哈希文件；本次只列文件，未恢复验证、未验证对象完整性 |
| 既有自动测试 | 解析既有 XML：235 cases，其中 229 passed、6 skipped、0 failures/errors；耗时 428.002 秒。隔离验证 23/23 依据部署交接。本次未重跑业务全量测试 |

当前证据：[现场清单](evidence/runtime-20261016T142915Z.json)、[管理连通性](evidence/management-connectivity.json)。第一次 SSH 尝试拒绝的记录也保留在 evidence 中，不用第二次成功覆盖失败事实。

## 2. 必须先修正或安排验收的事项

| ID | 优先级/性质 | 发现 | 处理及完成条件 |
|---|---|---|---|
| OPS-01 | P0，实测 | <prod-host> 临时自签证书只有 CN，未见 SAN；即使把该证书作为可信 CA，按 IP 校验也报 IP address mismatch | 提供院内正式域名与 SAN 证书，或含 IP SAN 的受信证书；客户端验证证书链和名称通过。删除运维验收对 `-k` 的依赖 |
| OPS-02 | P0，源码问题，恢复未执行 | restore.sh 的 pg_restore 单独调用 docker compose，没有公共 helper 的 `-f`/发行目录，执行结果依赖当前目录 | 统一 compose 入口；在任意工作目录、独立项目/空目录进行恢复演练 |
| OPS-03 | P0，源码问题 | restore.sh 先按当前 runtime.env 检查数据目录，之后才解包备份配置；备份内挂载路径可能与检查对象不同；缺失变量时空值也可能漏检 | 在临时目录校验归档、最终路径映射、镜像版本和空目标，再执行；禁止借恢复覆盖当前 /etc 或生产卷。路径为空、越界、符号链接绕过、非空目标均失败关闭 |
| OPS-04 | P1，源码问题 | backup.sh 先 dump PG/exchange，随后才询问并暂停 worker 打对象包；不构成多存储共同静止点。失败没有恢复服务的 trap；exchange/objects 路径有硬编码 | 静止窗口应先于所有快照并覆盖全部写入口；记录一致性口径；失败恢复原先运行服务，不擅自启动原本停着的服务；读取统一路径配置 |
| OPS-05 | P0，实测 | <prod-host> NTP=yes 但未同步；DMZ chrony 未同步，DMZ 时间比本机约慢 32 分钟 | 配置院内 NTP 并留偏差证据；平台签名仍由 <prod-host> 产生，DMZ 时差也影响证书与审计 |
| OPS-06 | P0，实测/未实现 | <prod-host>→DMZ443/8443 拒绝；DMZ本机没有443/8443监听；强制 IPv4 HTTPS 平台出口超时 | 开通 DMZ→eco.taobao.com:443 出口及 DNS，部署专用转发，验证双段 TLS；80通不能替代443验收 |
| OPS-07 | P1，未验收 | 有备份文件，但没有独立空环境恢复、异机备份、恢复时长证据 | 完成含合成对象的独立恢复，核对配置/令牌/PG/交换/对象/任务；异机受限加密副本、保留期与RPO/RTO由院方签定 |
| OPS-08 | P1，未验收 | 未做服务器重启恢复、外部告警、实际客户端浏览器与合成业务闭环 | 在维护窗口演练，日常采集/发送保持关闭；不可为了演练终止共享端口或清库 |
| DTR-BASE-01 | P0，源码边界 | 现有发送与回执处理直接构造 MockHttpClient；启用非 Mock 目标不代表有真实 Alibaba 实现 | 增加明确适配器注册，未知目标报不支持；按冻结目标ID选择，不能取第一个 PUSH 目标 |
| DTR-BASE-02 | P0，源码边界 | manifest仅pathology/metrics，worker jobs/events/工作流分支有硬编码；通用 payload 仍偏病理 | 增量扩展领域注册、任务处理和药品 payload，不能只放一个新目录就宣称插件接通 |
| DTR-BASE-03 | P0，源码门禁缺口 | production_allowed 与 _guard_production 只判断是否存在 OPEN 阻断；bootstrap不创建阻断，空表可能放行；旧守卫也没有按领域能力筛选 | 药品正式采集/发送必须验证必需证据集合与批准revision；空证据拒绝。保留病理既有阻断规则，不能批量关闭 B01—B09 |

源码位置：`implementation/deploy/linux/{backup,restore,status,lib}.sh`；`implementation/plugins/{app_runtime,core_handlers}.py`；`implementation/apps/worker/engine.py`；`implementation/apps/core_api/app.py`；`implementation/apps/bootstrap.py`。以上源码问题是静态审查发现，尚未在目标机触发故障。

## 3. DMZ 最新状态和网络解释

本次匹配已有 SSH 主机指纹后登录 <relay-net-host>。HTTP HEAD 到旧网关返回 200；HTTPS 默认解析尝试 IPv6时报网络不可达，随后**明确强制 IPv4仍连接超时**。因此不能仅归因为 IPv6，也没有证据证明443已放通。

DMZ→<prod-host>:22 拒绝，443及8443 TCP探测成功；但同一轮<prod-host>本机未监听8443，可能涉及中间设备，TCP成功不能认定命中了应用。管理可继续用已验证的<jump-host>二跳，业务转发不依赖DMZ反连SSH。

DMZ为Anolis7.9，未发现python3/nginx/docker命令；存在8001旧服务，归属待核实，下一AI不得占用或停止。部署须选经兼容性验证、可离线安装的受支持转发运行材料；不能照搬<prod-host>的Docker包。

证据：[首次DMZ探测](evidence/dmz-evidence-20261016T143339Z.json)、[强制IPv4复测](evidence/dmz-evidence-20261016T143409Z.json)。旧版timedatectl不支持所用property参数，该条命令退出1；未同步判断另有chronyc的 `Not synchronised` 证据。探测脚本退出0仅表示取证完成，不表示所有网络项目通过。

## 4. HIS 与接口开发尚缺什么

此前按用户提供的216连接说明，已识别<relay-jump>数据资产平台受控只读路线；当前登记服务名`his`、用户曾给`hisuser`、说明示例`orcl`存在冲突。现有连接注册为`medical_dict_push`，不满足选用的只读接入规则；不能取出平台现有密钥绕过门禁，也不能自行改注册策略。

已获得的7表232字段仅为<relay-jump>上的目录元数据，不是HIS当前活库验证。`MED_TRACE_RECORD`、处方追溯码表、出入库追溯码表、处方主表等可作候选，但稳定主键、发药完成、退药、修改/作废传播、父子包装和跨药房关联仍需HIS人员确认。不能直接按字段相似写JOIN上线。

SDK类存在不等于该医院已获API权限；平台AppKey/AppSecret、机构企业标识、官方SDK版本、业务字典、合成响应和首次真实联调范围均未齐备。尤其供应商出入库与患者发退药需要独立profile。详细待办与执行顺序见[开发计划](DEVELOPMENT_PLAN.md)。

## 5. 本次复核没有做的工作

没有重启/升级/写服务器，没有修改HIS/数据资产平台，没有提交真实业务或测试单至正式平台，没有重新执行历史回归。HTTPS请求因证书名称不符在TLS阶段终止，本次不声称已重新验证登录接口或前端HTTP结果。开发计划中的任务、接口、视图与验收均是下一阶段要求。
