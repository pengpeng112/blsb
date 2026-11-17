# 历史清单与两视图实时核查交接

任务DTR-HISTORY-LIVE-COMPARE-20261023；session `20261023T040459Z-f796cea9`；工具Codex，模型unknown；2026-10-23。
用户明确授权当前白天只读查询，后续“继续”保持此任务；此前异步窗口问题已由本次授权解决。

## 完成和证据

[详细报告](../../development/drug-trace-history-live-compare-20261023/README.md)、[L01—L06完善计划](../../development/drug-trace-history-live-compare-20261023/MODIFICATION_PLAN.md)。
25份489613行历史记录，473614个码/489582个旧明细号＋码组合全部实际查询。484536行同键且次数匹配；5077行未匹配，涉及4974个另一格式旧单号及531码，其中3919行的码仅在其他明细存在，1158行的码在两视图不见。
1396个匹配组合当前UNKNOWN，关联411主单，原状态全退1350、FLAG=1且退费46；应保留正常发送拦截。31组/62行同单同码确实有不同源记录ID。调用现有纯质量函数：普通1码样本VALID；重复组所在12行整单INVALID/QD-DUP-CODE。
不认定5077条必然漏报或应补报；无法仅凭单号格式判断住院/拆零。专用reader不能查被JOIN排除的原记录，已交付两条DBA参数化小样SELECT，未执行。

## 实际命令和结果

以下均在F:/python/blyb运行，退出码除明确说明外为0：

```powershell
python docs/development/drug-trace-history-transfer-audit-20261023/probe_history.py --approved-daytime
python docs/development/drug-trace-history-live-compare-20261023/run_compare.py --approved-daytime
python docs/development/drug-trace-history-live-compare-20261023/run_compare.py --approved-daytime --diagnose
python docs/development/drug-trace-history-live-compare-20261023/build_private_workbook.py
python docs/development/drug-trace-history-live-compare-20261023/verify_delivery.py
```

实时执行经sjzc/<relay-jump>受控OracleConnector，身份BLYB_DRUGTRACE/服务his，仅CREATE SESSION与两视图SELECT，无角色。先3键小样8条SELECT约0.21秒；全码949批加9条身份/元数据SELECT约52.242秒；状态补查5批加1身份SELECT约0.765秒。合计972条SELECT，批次最大1872行/约0.121秒，无触顶或查询错误。每次30秒/10000行限制，累计初始预算600秒，没有全表COUNT。
详见三份JSON回执；完整标识只存仓库外受限JSON和新Excel，查询参数未回显到共享结果。原SQLite只读打开，统计临时库在RAM。
最终离线验证：6535行差异标识/位置、20位码文本、25份原件SHA、私有ACL、5份Python AST、两条DBA单SELECT静态验证均通过。真实质量函数本机纯调用2例有结果，未运行产品全量测试，因为无业务代码更改。
收尾曾有一次临时标识断言失败；使用落盘验证脚本完整回读6535行通过，未发现Excel内容或文本类型异常。ACL验证初版调用Windows PowerShell5，受模块环境影响未能加载Get-Acl（退出1）；改用当前pwsh并加Stop错误处理后4个路径均验证通过。没有因此修改权限或覆盖工作簿。
文档目录清单和新增/修改链接的最终校验另见本轮documentation-validation.json。全仓既有234个新增断链为前轮已登记基线，不将其宣称为通过。

## 改动与保留

新增独立报告/计划、批量核验和诊断工具、受限工作簿生成/离线检查、两条DBA SQL与聚合证据。更新共享状态/待办/交接及两个业务README/旧报告入口；共享文档修改前副本在本轮before目录，以txt保留。
原Excel、旧SQLite/核对工作簿、历史冻结包、生产r10视图/插件/配置均未覆盖。HIS写入0、生产修改0、平台访问0、新上传0。既有1单4码成功不撤销、不重发、不重置额度。

## 下一步

先L01：DBA使用受限Excel挑选3组未匹配键，执行SQL01/02确认原记录和三键主单关联。当前账号权限限制是根因进一步确认的边界，不改用高权限身份绕过。L02是否补其他来源由真实业务范围及关联证据决定；L03保留退费拦截；L04核实重复真实原因；L05继续CREATE_TIME同口径审计及A01—A05齐套/晚到；L06保留历史防重，不自动补报。
本轮完成的是全部历史键一向对应与次数核查和修改建议；没有同CREATE_TIME窗口全视图反向穷举、同SCN快照或每码平台终态认证。
