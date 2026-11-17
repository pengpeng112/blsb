# DTR-SOURCE-DELIVERY-20261022

工具/模型：Codex / unknown。session：20261022T094444Z-de110dcf。结果：handed_off（本轮来源开发部署完成，正式发送仍缺实际外部输入）。

## 修改与实际实施

1. 在原业务源码布局增量修复Oracle主单NULL首游标；承接前轮码明细NULL修复一并部署。新增r10独立受限profile及逐连接reader核对，旧r1/r2/r7不改。
2. 新增source_config.py及三条有角色、租户、证据与审计的生命周期API。新来源固定视图、reader、env引用、batch；不读原服务器托管凭据内容，不把广源medical_dict_push改标只读。
3. 为既有FB底包制作离线增量发行src-20261022T103000Z；依赖freeze无变化。101000Z只staging未安装；103000Z包含恢复保护修复。
4. 经既有固定SSH指纹的<jump-host>跳板传包/目标哈希校验，备份20261022T095541Z（含对象）→修复版restore独立根validate-only→install升级→worker安全绑定专用reader。
5. 真实worker登录HIS/权限核验；一秒窗口确认唯一候选，NULL首屏和整单码分页实际可用。经现有admin API批准新三字典revision及1天collect证据，再发布/启用来源。
6. API采集1张4码DONE/VALID，API冻结PREPARED。实际perform_send(dry_run=True)五必填通过且码原文保持，attempt/quota/send_job0；未调用真实发送API。
7. 新worker严格mTLS→DMZ→平台查询成功；查无本系统新单号不作为旧厂商未报证明。DMZ非root服务active、仅query、库存关闭；未修改DMZ配置/时间/白名单。
8. 更新STATE/BACKLOG/HANDOFF、四类实施文档及外部清单，保留旧材料；账号/视图已成功，不再让用户重复执行DDL。

## 真实命令/结果

- python -m pytest tests/integration/test_drug_trace_oracle_first_page.py tests/integration/test_drug_trace_readiness_fb.py -q：41 passed，退出0。
- 六药品文件相关回归：132 passed，退出0，见source-delivery-regression.xml。
- python -m pytest tests -q -ra：424 passed/8 failed/7 skipped，退出1；原始full.xml/log保留。
- 运维文件新增3反例后首轮16项中10失败/6通过（GBK解码UTF8子进程导致None）；仅设置进程PYTHONUTF8=1及既有Git Bash PATH，原运维文件16通过/退出0，安装测试文件9通过/退出0。未删/弱化断言；未称另跑过完整全量绿灯。
- python docs/development/drug-trace-source-deploy-20261022/build_patch_release.py：两次不同revision构建均退出0，最终backend sha256:269792e1fc1b1f500a8dd66fb28990d5eb938743f38a43d4601c62ae91c06fbe。
- 使用数据资产既有venv执行deploy_source.py：preflight（首轮只读表名写成submissions失败，改正submission后0）、transfer/backup/restore-check/upgrade/bind/verify_worker/setup/collect/prepare/prepare_worker/dryrun_worker均成功。
- 偶发SSH TCP Connection refused保留并仅做一次传输重试；无密码试错。旧恢复预检的too many arguments不是通过证据，独立子任务修复后新receipt无误通过。
- postcheck.py：最终六healthy/root600/core无DSN或AppSecret，DMZ只有query、NTP未同步、正式证书未到。首轮DMZ配置路径误用config.json，r2纠正forwarder.json后全部只读命令0；旧失败receipt保留。
- git diff --check：退出0，仅既有换行提示；本轮脚本编译通过。未提交/重置/清理既有脏工作区。

## 现场边界与下一步

本轮HIS写0、上传0，未开启连续采集或自动发送。来源/字典/collect及建包已落地，真实发送未完成。
仍需正式CA/SAN/clientAuth、持续NTP、uploadretail授权和321/323最终口径、具体未报首单与max_bills=1范围；既有凭据和服务期不重复索要。
按README最后章节启用DMZ必要retail方法、正常绑定send证据/启用目的地后再发一次→终态→逐码核验。
恢复演练显式给生产根外data-root；此轮只validate-only，不是实际生产恢复。

详细证据、API、发行/备份/回退和机器状态：
[本轮README](../../development/drug-trace-source-deploy-20261022/README.md)。
