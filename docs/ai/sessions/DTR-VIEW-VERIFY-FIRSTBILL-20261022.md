# DTR-VIEW-VERIFY-FIRSTBILL-20261022

- 工具/模型：Codex / unknown。
- 会话：20261022T081403Z-6c75fdeb。
- 当前用户授权：SQL已执行；将账号/密码/视图保存本地；即时只读核查，无问题后上传1张。
- 范围：docs/ai、docs/development/drug-trace-view-verify-20261022、对应.ai/private目录。
- 状态：核验与本地登记完成；真实上传未执行，条件不满足；交接后续修复/业务确认。

## 已实际完成

1. 本机DPAPI保存新账号凭据；connection.json登记<his-host>:1521/his及PHARMACY两视图，
   ACL仅当前Windows用户/SYSTEM、Git忽略。已保存共享状态修改前基线。
2. 完整读取sjzc技能及HIS只读/访问规则；通过平台注册端点、<relay-jump>容器标准OracleConnector.execute_readonly，
   使用用户本次提供的新专用账号实际登录，未读取服务器已有凭据内容。
3. 16项有限SELECT全部成功：用户BLYB_DRUGTRACE、service his、SESSION_PRIVS仅CREATE SESSION、角色0，
   两视图直接SELECT及私有同义词、17+18列VALID。实际owner PHARMACY，区别旧r8拟定BLYB_RO。
4. 当天20张主单/100行码样本，码全部20位数字。选其中一张复核1头4码，无重复记录/码、revision一致。
   不导出患者字段、原始码或单据键；返回本地前已做汇总。
5. 同一真实单据在Oracle旧首屏谓词读0、NULL安全谓词读4；部署worker也确认缺修复。
   业务kind=UNKNOWN，finalized为空；质量规则明确WAITING。未把数量/层级可选值误判平台必填。
6. 新版现场探针application-r2 SSH0/worker0，6healthy、真实配置PUBLISHED、签名true，
   状态查询经worker/mTLS/DMZ/平台成功1次；来源DRAFT无DSN、目的地DRAFT、医院药房映射空，采集/send缺。
7. DMZ仍仅query.billstatus；官方HEAD200、时钟接近官方Date，NTP未同步/测试链仍在。未更改远端配置。
8. 平台相关状态值域登记0条，离线包过期；已向用户异步询问业务完成/撤销口径和具体未报测试单，未替其批准。

## 命令/结果与证据

- `python scripts/ai_session.py status` 后start，退出0；登记无进行中冲突。
- 数据资产backend Python执行`verify_reader.py r1`，退出0，16条SELECT成功。
- `recheck_runtime.py application` 首轮SSH255无结构结果；保留application-r1，不称成功。
- `recheck_runtime.py application r2` 退出0、SSH0/worker0/平台只读查询成功。
- `recheck_runtime.py dmz` 退出0、SSH0、官方HEAD200。
- <relay-jump>平台值域元数据SELECT成功、0行，不登录业务库解析值域。
- 本地静态/链接/契约检查见本任务evidence/local-validation-r3.json；r1因标点旁空格误报差异，
  r2改完整SQL token对比，两视图实际SELECT一致，但自身报告链接在创建前检查失败；
  r3修复报告预留顺序后通过全部检查，原失败保留。没有运行或宣称全量业务回归。

完整解释及逐项下一步见[核验说明](../../development/drug-trace-view-verify-20261022/README.md)。

## 下一步

不要再建账号或照旧r8包落到BLYB_RO。按实测PHARMACY两视图及本机DPAPI凭据接续。
先修Oracle首屏SQL并验证/构建/授权窗口升级；确认状态组合/完成及撤销口径，另建revision，
绑定新只读源与药房企业字典、启用来源/目的地，整单采集质量VALID；
落实uploadretail方法权限、转发白名单/证据/首张未报scope，按正常流程max_bills=1发送和查询终态。
用户已授权符合条件后测试，不重复问是否开始；不绕过业务未知或把随机候选当未报确认。

实际副作用：本地文件新增；HIS写0，源登记/远端配置修改0，真实上传0。
