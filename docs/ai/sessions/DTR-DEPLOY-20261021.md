# DTR-DEPLOY-20261021 会话交接（ZCode / GLM-5.3）

任务：按 `docs/deployment/drug-trace-install-20261021/NEXT_AI_DEPLOY.md` 完成安装前修复、
重打包、DMZ 与 <prod-host> 部署启动及验收。结果：**L1 完成（DEPLOYED_TEST_ONLY，禁发）；
L2/L3 如实 BLOCKED；真实上传 0**。完整结果见同目录 [EXECUTION-REPORT.md](../deployment/drug-trace-install-20261021/EXECUTION-REPORT.md)。

## 改了什么（代码，全部在 implementation/）

- **P01**：新增 `plugins/sources/db_view/oracle_client.py`（Thick 进程内一次初始化，
  失败粘滞阻断、不回退 Thin、显式 ALLOW_THIN 开关）；`drug_trace.py`/`warehouse.py`
  真驱动路径接线；`requirements-linux.txt` +oracledb==3.4.2；`Dockerfile` IC 19.32+
  libaio 兼容层（trixie libaio.so.1t64→软链）；`deploy/oracle/README.md`（来源/哈希，
  zip 不入 Git）；`drug-trace.override.yml` 修正 Thin 注释+CLIENT_LIB_DIR。
- **P02**：`deploy/linux/lib.sh` 新增 `BLYB_DRUGTRACE_ENABLED` 持久开关
  （runtime.env）+固定覆盖路径白名单；compose/compose_project 同闭包；缺件 fail-closed。
- **P03**：`deploy/linux/certs.sh` make-test-ca 补 CA:TRUE/keyCertSign/SKI 扩展
  （OpenSSL 3.5 严格链校验要求；<prod-host> 实测踩出）。
- **P04**：`deploy/dmz/install.sh` port_listener_owner/listening_on 重写（awk \b 与
  sed 顺序错误）；`systemctl show --value`（219 不支持）改 cut 取值。
- **P05**：`apps/init_drug_trace.py` --tenant（多租户拒绝默认第一）。
- 测试：新增 `tests/integration/test_dpl_deploy.py`（9 项）；
  `test_remediation_round2(_b).py` R22 fake oracledb 补 init_oracle_client/clientversion
  （断言语义不变）。

## 真实命令/结果（要点，全量见 evidence/）

- 局部：test_dpl_deploy 9/0；相关 6 文件 77/4；全量 `python -m pytest tests -q` =
  **380 passed/2 failed/7 skipped**（2=R22 旧 fake；补齐后两文件 56/0；XML
  var/dpl-20261021-full.xml）。
- 镜像：docker build→`oracledb.init_oracle_client` thick/clientversion 19.32.0.0.0/
  is_thin_mode=False（UID10001）。
- 打包：app `python deploy/release/build_release.py --out var/rel-dpl --release-id
  dpl-20261021T111000Z`（148 文件）；DMZ `pack_dmz_release.py`（110326Z→修复后 113750Z）。
- DMZ 现场驱动：`docs/deployment/drug-trace-install-20261021/deploy_dmz.py`
  （precheck/config/install/verify；指纹固定 SHA256:3g4LnnNM…）。
- <prod-host> 现场驱动：`deploy_8156.py`（backup/secrets/upgrade/postcheck；密钥经
  with-credentials.ps1 授权子进程；pty base64 单行安全通道）。
- 现场：DMZ install→verify→--with-upstream→restart 全过；<prod-host> backup→secrets→
  install→postcheck 全过（详见 EXECUTION-REPORT 各节与 evidence/*.json）。

## 未完成 / 下一步

1. **DTR-CERT-ROTATE**：院方正式链替换 TEST-ONLY（pki-r3 现网；轮换时两机同步）；
   DMZ NTP 时间源（仍慢约32分钟，未校时）。
2. **DTR-L2-SOURCE**：DBA r7 两视图+readonly→限量核验→真实 SourceProfile→DSN 注入启用采集。
3. **DTR-L3-FIRSTBILL**：平台审批流发布真实 destination ConfigRevision（现仅 mock DRAFT）→
   应用路径只读签名查询（EXT-07）→321/323→首张 max_bills=1。应用正式路径查询在配置发布前
   保持 BLOCKED（DMZ 出口诊断 SUCCESS 不冒称应用门禁）。

## 注意事项

- 事件披露：TEST-ONLY client.key 内容曾进入一次本地诊断输出（feed_file 失败路径打印了
  远端文件）——已当场全链轮换（pki-r3，含 CA）并双机重放，本地日志删除；CA/服务端/ops-probe
  私钥未外泄；证据目录 Secret 明文扫描 SECRET_HITS=NONE。
- DMZ 回退注意：previous_version=110326Z 的 install.sh 仍带 `systemctl --value` 缺口，
  如实际回退需手工取 MainPID 或直接回 113750Z 重装。
- 现网禁发三重保持：DMZ allowed_top_methods 仅 query.billstatus；DSN 空；send 证据门禁。
  未改 SIGNATURE_VERIFIED、未发真实业务请求、未动 HIS。
- pty 多行文本传输曾出现同长异字节（已改单行 base64 通道）；后续经二跳送文件沿用
  deploy_8156.feed_file。
