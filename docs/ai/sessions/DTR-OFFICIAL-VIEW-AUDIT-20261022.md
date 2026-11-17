# DTR-OFFICIAL-VIEW-AUDIT-20261022

- 工具/模型：Codex / unknown。
- 会话：20261022T144533Z-d45907d5，顺序接力。
- 用户授权：重新读官方指南，核查现有视图，可以只读查询数据比对；未要求新增上传。
- 修改范围：本轮核查文档/查询脚本、流程说明及本机完整凭据副本正文、共享AI入口/状态/待办/交接。无业务源码或线上配置修改。

## 结果

[核查报告](../../development/drug-trace-official-view-audit-20261022/README.md)。
公开官方正文HTTP200，39,446字节，标题归一后与前轮官方快照完全相同；同时读官方API52553，五必填及321/3+51解释吻合。
HIS活库27条SELECT成功，专用BLYB_DRUGTRACE仅登录＋两视图SELECT、无角色；视图VALID/17+18列且与r10定义一致。
30头/100码样本、8张整单11码无主从版本/时间/记录重复/同单码重复问题。
新增实际证据：100条明细修改晚于发药20～251秒（89条>60秒）；主单时间不包含该变更，expected_code_count恒空。

适用结论：正常已完成未退费门诊batch可继续用；不能据此验收全历史、持续晚到变更、退药/住院/拆零。
已登记A01—A05完善项：齐套及码集合变化、人工历史完整性、整单解析策略（当前未传ignore_part_success_flag）、包装/扩展业务、明细PENDING展示。
既有1单4码平台3/51成功事实保留，不撤销、不重发。

## 实际命令及证据

- `python scripts/ai_session.py status`：退出0；已读现有接续记录，未启动子代理。
- 通过Python urllib读取用户给的公开语雀页面及公开markdown入口，HTTP200；原生web语雀失败，成功与失败路径均记获取回执；官方API页通过web读取。
- SQL静态门禁使用既有`validate_his_sql.py`：运行前11字面模板通过；实际执行的27条完整SQL事后复核通过。受控连接器逐条执行只读门禁。
- `python docs/development/drug-trace-official-view-audit-20261022/audit_views.py r1`：退出0，25条成功，证据`evidence/reader-r1.json`。
- 第二次通过同一reader基底执行`audit_late_changes.remote.py`内容（身份＋100条时间投影）：退出0，2条成功，`evidence/reader-r2.json`。不是第二次上传。
- 实际视图定义与用户r10 SQL按token比较：2/2一致，`comparison-result.json`。
- 本地链接检查：6份文档无断链；3个Python脚本语法解析通过；私密副本正文与最新流程说明一致；Git忽略及ACL检查通过，仅当前用户/SYSTEM（文件继承受限目录ACL）。
- ACL自动回执首轮PowerShell表达式解析失败，改为带括号的WindowsIdentity取值后重试退出0，user/SYSTEM-only及父目录ACL保护均True；未涉及凭据内容。
- 无应用代码变更，未重跑应用全量回归；不声称本轮新增全量测试通过。

查询窗口北京时间2026-10-22 `[00:00,次日00:00)`，30头/100码、最多8单/每单501行，单条45秒、总预算150秒。
原广源登记write_policy仍medical_dict_push，未使用其托管凭据；通过已有受控连接器注入本项目已授权新reader，不改平台注册。
完整码和源业务标识只留在远程进程，输出结构与汇总。HIS写0、源注册改0、线上改0、上传0。

## 下一步

交付用户清晰流程、受限账号密码文件、空白人工核对Excel及本轮报告。
收到人工历史Excel/CSV后按同业务时间/药房/源单据/码及次数双向核对，不自行补报。
开发续接按A01—A05登记任务与范围；若需源表侧差异核查另用当前授权的受控只读来源，不能为扩大权限读托管密码。
