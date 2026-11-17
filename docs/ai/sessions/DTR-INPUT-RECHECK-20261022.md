# DTR-INPUT-RECHECK-20261022

工具/模型：Codex / unknown；session：20261022T055542Z-ffdbe754。
任务：核对用户已提供资料，并实际测试可行部分，避免重复索取已登记资料。
范围：本轮docs目录、共享docs/ai、本轮受限私有证据；无业务源码或现场配置改动。

完整[核对说明与实测](../../development/drug-trace-input-recheck-20261022/README.md)。
重读FB交付、现有状态；已应用sjzc/HIS只读技能。平台源登记重新查得medical_dict_push，
两视图元数据0，源端HIS未连接；不能据此断言活库没建视图。

实测：fb-20261021T164800Z、6容器healthy、签名True、配置PUBLISHED/is_mock=False；
query/reconcile APPROVED，collect/send MISSING；worker DSN空、来源DRAFT、
医院药房字典draft-1空、业务单据/采集/submission/配额均0。目的地对象仍DRAFT，
plugin采集事件仅为ENABLED目标排PREPARE，尚需启用生命周期操作。

真实worker UID10001按现行query门禁及真实配置，经mTLS→DMZ→平台一次状态查询成功，
total_num=0；另Windows签名经SSH stdin→DMZ curl诊断service.getenddate成功，
business1/service11，基础版end_date=2028-12-31。有效期不再列待提供，上传权限仍未知。
DMZ TEST-ONLY证书/约慢32分/chrony Reach0/仅query白名单维持；现场零配置修改，上传0。

失败如实保留：首轮worker退出1；r2探针对query额外加配置闭包造成MISMATCH_CONFIG，
核对现行程序后确认为探针误设，非部署故障；r3按真实规则成功。没有修改证据绕过。
有效期首轮HTTP200后本地PowerShell5.1安全模块加载失败；公开样例定位后换pwsh，
新r2归档/解析成功（原始响应DPAPI、用户/SYSTEM ACL、Git忽略）。

平台请求总计3次只读（状态1+有效期2），无自动重试；HIS活库0、上传0、生产配置修改0。
具体命令为本轮recheck.py metadata/application/application-r2/application-r3/dmz及
凭据包装器启动service_expiry_probe.py、r2。证据均排他创建。
本地检查结果见本轮evidence/local-validation.json；未重跑业务测试，沿用FB上棒405/7/0。

下一步等待视图实施/受限只读登记及真实药房、业务规则、首张未报scope等输入，
并落实证书/校时/上传权限和321/323确认；已发异步澄清，但不以未回复作批准。
不再索取密码、AppSecret、ent_id或服务期书面材料。
