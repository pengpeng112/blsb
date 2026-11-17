# DTR-FINALIZE-20261020 交接（ZCode / GLM-5.3）

任务：一轮完成药品追溯剩余开发、测试、发行、安装工具及具备授权/窗口的现场禁发部署。
session_id：20261020T112042Z-eabc386d。范围：implementation、docs。
未提交 FIX2/FIX3/10-20 字节全部保留（git 工作区叠加本轮改动，无 reset/clean）。

## 改动清单

**DMZ（FIN-01/02/03）**
- `implementation/deploy/dmz/forwarder.py`：v2 重写——systemd FD 接收（LISTEN_PID/
  FD数/SO_ACCEPTCONN/AF_INET/地址端口全验证，不符即拒绝，不静默降级）；TLS 握手移
  worker 线程（慢握手不阻塞 accept）+ 握手前连接限额（max_pending_handshakes）；
  CL/TE 歧义、重复 Content-Length、非数字 CL、畸形 UTF-8、重复 TOP method 拒绝；
  分阶段预算（handshake/header/body/read/write/upstream）+ total_request_deadline；
  日志全脱敏（log_request 不含请求行；reject 不含 path；redirect 不含 Location）；
  /health mTLS 强制且不冒称上游；--diagnose [--diagnose-upstream] 分层 JSON；
  --check-config 严格校验（端口/预算/IP/白名单/证书配对/SAN 覆盖/EKU/有效期）；
  inout 方法默认关闭（enable_inout_upload 显式开启）。
- `blyb-dmz-forwarder.socket`（新）：ListenStream=@LISTEN_ADDR@:443、Accept=no。
- `blyb-dmz-forwarder.service`：219 兼容指令集（NoNewPrivileges/PrivateTmp/
  ProtectSystem=full/ProtectHome/ReadOnlyDirectories；无 Ambient/strict/append/
  *Paths）；@PYTHON@ 渲染为私有运行时；journal 日志。
- `install.sh`：v2 重写——manifest 哈希复核（含路径越界防护）、版本化 release +
  原子 current 切换、unit 渲染一致校验、严格 --verify（mTLS 健康 200 + 无证书必须
  拒绝 + 进程身份/解释器路径 + 8001 基线）、--with-upstream 独立出口码 4、
  升级失败自动恢复、--rollback（配置/证书不盲回）、--uninstall/--purge、
  /proc 直读端口归属（无 ss/ps 的最小系统可用）。
- `pack_dmz_release.py`（新）：发行打包（manifest+SHA256SUMS+运行时元数据+TEST-ONLY
  证书标记；LF 归一防 Windows CRLF 破坏 sha256sum）。
- `README.socket.md`/`README.md`（重写）。
- 运行时材料：cpython-3.11.9+20240415 x86_64 install_only（python-build-standalone），
  官方 sha256 78b1c16a9fd032997ba92a60f46a64f795cd18ff335659dfdf6096df277b24d5 核对通过；
  存放 F:\python\blyb\var\dmz-runtime\（不入 Git）。

**HIS 来源（FIN-04）**
- `docs/deployment/reporting-20261015/view-contract-r7.md`（新）+ `sql-r7/`
  （10-create-views.sql / 20-grants.sql / 30-verify.sql / 40-optional-index.sql，
  全部 DBA 审核草案；视图未建未验，r7=CANDIDATE）。
- `plugins/sources/db_view/source_profiles.py`：r7 档案（unresolved 含
  dba_reviewed_view_ddl_execution 等；r1/r2 原文未动）。
- `plugins/domains/drug_trace/business_rules.py`（新）：原始状态（PRESC_SOURCE/FLAG）
  → 分支批准规则登记（初始为空）；未批准 → UNKNOWN + QD-BRANCH-UNAPPROVED 质量等待；
  批准后重导同 revision 激活 bill 分类（快照原文不变）；持续增量模式
  require_continuous_mode（无 {kind,evidence_ref} 变更源即拒绝，AUTO 阻断）。
- `ingest.py`/`plugin.py`/`contracts.py` 接入。

**平台契约（FIN-05）**
- `service.py`：查询日期语义 query_date_semantics ∈ {both(默认),bill_time,
  upload_time}（批准 ConfigRevision 固定）；默认双窗口合并查询（历史 bill_time 与
  上传日期差月不漏查；任一窗 ERROR/UNKNOWN 保守传播）；reconcile 同口径。
- `build_client`：session_ref 端到端（env:/mock: 解析注入 client；file: 明确拒绝；
  缺省允许）。响应规范化矩阵补测试（空列表/空 model/权限错/未知枚举/部分成功）。

**应用配置（FIN-06/07）**
- `apps/init_drug_trace.py`（新）：幂等草稿初始化（dry-run 默认/--apply 草稿不发布）
  + --check + --readiness 分层（CODE/LOCAL_SERVICE/NETWORK/SOURCE/PLATFORM/
  SEND_SCOPE；UNKNOWN 不是绿灯）。
- `deploy/compose/drug-trace.override.yml`（新）：core+worker 加载 drug_trace；
  worker 独享 AppSecret（env:）/mTLS 证书只读挂载/Oracle DSN；渲染级断言
  storage/exchange/nginx/migrate/bootstrap 无 Secret。
- `routes.py`：readiness 响应加 layers；`DrugTrace.vue` 分层显示（含
  "NETWORK=UNKNOWN 不是绿灯"提示）。
- `deploy/linux/certs.sh`：issue-client / issue-ops-probe（clientAuth EKU 分离）。
- `deploy/linux/diag_time_dns.sh`（新）：219 兼容只读时间/DNS 诊断。

**测试**：test_dmz_forwarder +12、test_drug_trace_domain +10、test_fin_finalize（新）+5、
test_drug_trace_api readiness 断言更新、G3-16 按 FIN-A22 双窗口更新。

## 真实命令与结果

| 命令 | 退出码 | 结果 |
|---|---|---|
| 局部 9 文件回归（见测试执行记录） | 0 | 140 passed/1 skipped |
| `python -m pytest tests -q -ra --junitxml=var/dtr-finalize-full.xml` | 0 | **373 passed/7 skipped/0 failed**（740.61s） |
| `npm run build`（admin_web） | 0 | 70 modules |
| DMZ 打包（pack_dmz_release.py，含运行时+TEST-ONLY 证书） | 0 | 20261020T130019Z.tar.gz，sha256 ee9bc447…2250 |
| <prod-host> 发行（build_release.py，含镜像构建） | 0 | fin-20261020T130011Z，147 文件 |
| 容器首装/重装/升级/回退/卸载/重装（systemd239 PID1） | 0 | 全通过；停 service→verify=1；socket 再连拉起 |
| 最终 DMZ 包容器复验 | 0 | install=0 verify=0 verify-upstream=0（容器可出网） |
| DMZ 只读版本探测（SSH 固定指纹） | 0 | evidence/dmz-version-inventory-20261020.json |
| centos:7 运行时验证 | 0 | ssl/OpenSSL3.0.13/SNI/CA TLS1.3 OK |

## 现场状态（FIN-10/11：未部署）

用户交付本提示词未附维护窗口/现场变更委托；且正式 TLS 证书（EXT-04）、DMZ 出口
（仍超时）、平台口径（EXT-06/08/09）、签名资格（EXT-07）未齐。按计划
"没有窗口先完成全部本地实现和交付材料，不假称已上机"：
- <prod-host>：未升级（仍 20261016T114735Z）、未跑新迁移、未注入配置；零改动。
- DMZ：未安装（探测确认无既有 blyb-dmz）；8001=java 未触碰；零改动。
- 真实上传 0；HIS 本轮零查询；SIGNATURE_VERIFIED=False 不变。
部署材料与命令已就绪（两包+install.sh+runbook），窗口与授权到位后按
finalization README §FIN-10 顺序执行。

## 未完成/外部阻断（唯一剩余清单）

| 项 | 提供方 | 缺什么 | 影响 | 恢复命令/入口 |
|---|---|---|---|---|
| 维护窗口+部署委托 | 院方运维 | 明确窗口时间与两台变更授权 | FIN-10 两台禁发部署未执行 | install.sh（DMZ）；deploy/linux/install.sh+upgrade（<prod-host>） |
| 正式 TLS/mTLS 证书（SAN=<relay-net-host>） | 院方 PKI | 服务端/客户端/CA 正式链 | 只能 TEST-ONLY 隔离验收，不能正式链路 | certs.sh 产物替换 /etc/blyb-dmz/certs + <prod-host> 受控目录 |
| DMZ 出口 443/DNS/NTP | 网络科 | eco.taobao.com:443 放行+DNS+时间源 | UPSTREAM_BLOCKED | install.sh --verify --with-upstream（出口码 4=阻断） |
| HIS 生产 readonly+两视图 | DBA/HIS 厂商 | sql-r7 执行+限量核验 | SOURCE_READY 阻断 | sql-r7/10-30 + get_source_profile 新 revision |
| 分支/数量/完成规则批准 | 药学+信息科 | PRESC_SOURCE×FLAG 映射、拆零分摊、finalized | 未批准分支全部质量等待（可见） | business_rules.APPROVED_STATUS_RULES 登记+证据 |
| 签名资格（EXT-07） | 平台技术方 | 官方 SDK 或认可替代验收 | 真实发送保持阻断（SIGNATURE_VERIFIED=False） | 批准适配器 revision 附证据 |
| 321/323、查询日期、终态字典 | 码上放心 | 官方当期口径 | bill_type 哨兵保持 None；查询默认双窗口 | 批准字典/配置 revision |
| 首批 scope+既往上报核对 | 药学/信息科 | 具体未报单据+max_bills=1 范围 | 首张真实上传不可执行 | evidence API 登记 EXT-12 |
| <prod-host> 现场最终 systemd 219 验证 | 运维窗口 | 真实 219 上 socket 激活终验 | 已有 239 实测+219 静态白名单 | install.sh --verify |

**不是"只剩网络"**：证书/视图/规则/签名资格/字典/scope 均未齐，上表逐项可查。
证据索引：docs/development/drug-trace-finalization-20261020/evidence/
（dmz-version-inventory、dmz-container-lifecycle、dmz-final-package-validation）。
var/：dtr-finalize-local.log、dtr-finalize-full.xml、dmz-release/、rel-fin/、
ui-browser-20261020.png、dmz-release-pack.json。

## 下一步

1. 窗口到位：先 DMZ（TEST-ONLY 也可 LOCAL_READY）→ <prod-host> 升级（备份→迁移 001-011
   →插件）→ init_drug_trace 草稿 → 管理员发布配置/字典 → 禁发确认。
2. 材料（证书/出口/视图/口径）逐项到位后按 EXTERNAL_INPUTS_AND_NETWORK 清单核销。
3. 全部满足后 FIN-11 首张真实上传（max_bills=1，一次 POST→查询终态→码核对）。
