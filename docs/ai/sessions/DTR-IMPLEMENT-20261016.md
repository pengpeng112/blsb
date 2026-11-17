# DTR-IMPLEMENT-20261016 · 药品追溯离线开发（W00—W09）

工具 ZCode；模型 GLM-5.3。Git 基线 `ceb0b67`（含上一计划会话文档）；本轮提交
`e6835c9`(W01) → `cebc014`(W02-W05) → `4069894`(W06) → `5eca465`(W08) → 收尾提交。
会话：20261016T150745Z-15ecdb75。范围：implementation、docs/ai、docs/development/drug-trace-20261016。

## 实际做了什么

按 docs/development/drug-trace-20261016/DEVELOPMENT_PLAN.md W00—W09 顺序实施（W10/W11 现场项不在本轮）：

- **W01 运维 P0**：`deploy/linux/certs.sh`（离线测试 CA+SAN 服务端证书+校验，名称不匹配拒绝）；
  `status.sh` 去 `-k` 验收（CA+SAN 校验，无 SAN 判失败）；`restore.sh` 重写（只读校验先行：哈希/归档
  穿越/符号链接/备份 env 必需变量/镜像兼容提示；默认隔离演练=独立项目+独立数据根/配置根+演练端口
  127.0.0.1:18443；生产路径重叠双确认拒绝；空目标拒绝；`--validate-only` 零写入；统一 compose 入口）；
  `backup.sh` 重写（先静止 worker/core→排空→同窗内 PG/交换/配置/对象快照→恢复原运行服务；EXIT trap
  兜底只恢复原运行者；backup.json 记静止窗口）。runbook《备份与恢复》《TLS证书与验证》更新。
- **W02/W03**：`plugins/domains/drug_trace/`（contracts/models/plugin）；迁移 009（显式编号，SQLite/PG
  双测；不加 destination 列——用既有 profile_revision 派发保旧 schema 兼容）；app_runtime manifest 增
  drug_trace（attach 改列表语义）；compose `BLYB_PLUGINS=pathology,metrics` 显式固定（升级不自动加载）。
- **W04**：`sources/db_view/drug_trace.py`（Oracle11g ROWNUM keyset 模板+合成仓储；非 SELECT 阻断）；
  `ingest.py`（整单读取翻页到短页+版本复核重读；单事务快照/去重/码索引/血缘/质量/outbox/checkpoint；
  lookback 重叠回看）；`quality.py`（0/1/N、重复行/同单重码、原值、阈值阻断不截断、F/S/T 边界显式化）；
  `payload.py`（bill_code 稳定映射+批准字典才映射）。
- **W05**：`destinations/alihealth_drugtrace/`（protocol：TOP 表单/MD5 签名/响应解析/错误分类——
  **SIGNATURE_VERIFIED=False 硬阻断真实签名（EXT-07）**；transport：固定 DMZ、无重试、分层超时；
  client：两上传+查询）；core_handlers 目标适配器派发（真实目标未注册即报错，不回落 Mock）；
  worker job/事件注册表合并；UNKNOWN 自动排查询。
- **W06**：`deploy/dmz/`（stdlib forwarder：mTLS+源IP白名单+固定上游/路径/方法、原字节、无重试/缓存、
  限流/大小/并发、脱敏日志；systemd 最小权限单元；离线 install/verify/rollback；Anolis 材料 README）。
- **W07**：service（证据门禁 fail-closed：空表/缺项/撤销/过期全拒绝；Mock 离线通道除外）+ routes
  （/api/v1/drug-trace 全套）+ 对账（三方差异分类、处置需理由+角色+审计、重跑幂等、码级诚实标注未核验）。
- **W08**：`DrugTrace.vue`（就绪/证据、采集、单据（掩码）、dry-run 预览、发送 confirm、查询、对账处置、
  MOCK 显著标识）；浏览器合成闭环实测通过（截图在 evidence/browser/）。附带修复：Login 后端地址
  捕获时序（本轮以 localStorage 设置规避，未改组件）、处置 id 键、Mock 默认身份、证据门禁先于预检。
- **W09**：全量回归 **277 passed / 6 skipped / 0 failed**（XML：implementation/var/drugtrace-regression-
  20261016.xml）；发行白名单 +deploy/dmz/ 并试构建验证（135 文件+镜像）；四类实施文档更新
  （API矩阵/外部阻断/实施状态/测试记录）。

## 真实命令与结果（关键）

- `python -m pytest tests -q --junitxml=var/drugtrace-regression-20261016.xml` → 277/6/0，exit 0（两次运行）。
- 局部：test_drug_trace_ops 13、test_drug_trace_domain 24、test_drug_trace_api 5、test_dmz_forwarder 5、
  test_drug_trace_pg 1（真实隔离 PG16 容器 127.0.0.1:55432，即起即删）。
- `npm run build` → ✓ 158.5 kB。
- `python deploy/release/build_release.py --out var/release-check --skip-frontend` → 135 文件+3 镜像 ✓（已清理）。
- 浏览器：serve_drug_trace_ui.py(8765) + IAB 驱动；闭环见 docs/development/drug-trace-20261016/evidence/browser/。

## 与验收矩阵的对应

- 离线已测：OPS-01（SAN工具/无-k）OPS-02/03（演练/拒绝矩阵）OPS-04（静止窗口/trap）；
  DTR-A01—A16（除 A15 已由租户过滤+掩码间接覆盖，未逐一造越权用例——诚实登记）；
  DTR-S01—S08；DTR-B02/B03/B05/B07；DTR-C01—C05（C05 现场部分待 EXT-11）；DTR-D02—D04；
  DTR-E01/E02（浏览器）/E03/E04（构建+白名单）。
- 现场待验证（W10 范围）：OPS-05/06/07/08、DTR-C05 现场、E05（<prod-host>+DMZ 合成闭环）、E06 运维告警。
- 外部阻断不变：EXT-01—12 全部 OPEN；真实签名被代码硬阻断（SIGNATURE_VERIFIED=False，EXT-07 关闭后
  需官方向量对照测试置真）。

## 未完成 / 下一步

1. W10（需维护窗口+材料）：<prod-host> 升级到新发行（先 backup.sh 静止备份；BLYB_PLUGINS 保持 pathology,metrics
   直到启用药品）、DMZ 部署 install.sh、NTP/网络/证书 EXT 项关闭、现场合成闭环 E05。
2. W11（需单独真实发送授权）：首批真实单据→查询确认→三方核对→签认。
3. 登录页后端地址时序为已知小瑕疵（打包版首次登录需在“后端地址”填对或预设 localStorage；同源部署注入
   VITE_CORE_DEFAULT 时不受影响）——登记 BACKLOG，不影响本轮验收。
4. A15 专项越权用例（跨租户直查 drug_trace 端点）补强；reconciliation 的 profile 过滤当前按目的地覆盖
   两 profile 单据（scope 记录 profile，未按 profile 过滤药房分支单据——见 routes/service 注释，D 类已过
   但范围语义待 W10 前收紧）。

## 现场改动

无。本轮未登录 <prod-host>/DMZ/HIS，未发送任何真实数据；全部证据为本机（Windows+Git Bash+Docker Desktop
隔离容器）离线产生。凭据文件未读取未引用。
