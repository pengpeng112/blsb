# DTR-UPLOAD-FLOW-20261026 · 上传与限流改进交接

工具Codex，模型unknown；会话20261026T125856Z-42c112e3。用户要求改进并按接口上传，本轮延续现有定时授权，未扩范围。初始已读START_HERE/STATE/HANDOFF及工作区约定；没有并行代理。修改范围为登记的implementation、docs/ai、docs/catalog、任务运维目录、业务README，并按协议扩登记既有定时driver单文件。

[完整实现、真实证据和恢复步骤](../../operations/drug-trace-upload-flow-20261026/README.md)。上线版本flow-20261026T132000Z，基于ui-clarity-20261026T063000Z；manifest只含11个实际源码/部署差异，无依赖变更，无业务库迁移。另单独更新SHA受保护的宿主driver，DMZ单独只替换forwarder。既有基线在任务baselines和服务端私有维护目录，其他AI的未提交工作保留。

## 已实测

- TOP7/429完整错误与等待提示，跨进程持久节奏、共享冷却、触网前额度预检、真实查询延迟/退避、日期窗合并、终态跳过、原job fenced延后、driver等待适配、DMZ Retry-After，详见README。
- `python -m pytest ...test_drug_trace_upload_flow.py ...test_drug_trace_transport_pacing.py ...test_drug_trace_fix3.py ...test_dmz_forwarder.py -q -ra`：69通过1跳过，exit0；API/driver局部54通过exit0，PG专项1通过exit0。完整命令、PATH和XML见README。
- `python -m pytest tests -q -ra --junitxml=var/upload-flow-regression.xml`：522通过/7跳过/0失败/0错误，exit0。7项环境/大型压力跳过明列README，未声称全环境通过。
- `python docs/operations/drug-trace-upload-flow-20261026/build_release.py`：离线构建exit0、pip freeze相同；transfer_release.py：固定主机指纹/SHA传输成功；`check.py app upgrade`标准升级并安全更换driver，最终upgrade_status exit0、timer恢复0。
- `check.py dmz dmz_upgrade`：初次维护父目录缺失保护退出，无现场变更；只读确认、仅创建指定父目录后重跑成功exit0，原发行保留。新DMZ源码SHA绑定文件一致、check-config0、active。
- `check.py app smoke`：生产运行时临时合成TOP7一次假网络、第二次零触网；正式一笔已确认单只读查3/51，1页1窗。新共享目录真实0700且创建状态文件。`check.py app health`：新DMZ实际mTLS健康200，上游0。
- `check.py app verify`最终exit0：六healthy、原授权APPROVED、UI200且JS/CSS hash一致、timer active/waiting、旧247摘要/配额/断点不变。没有新上传、没有HIS查询、没有业务状态写回。

## 实现未测与待办

21:36最后源码校验：线上worker的9个变更运行文件与冻结包SHA逐一一致，driver LF版本SHA与部署绑定一致；本机CRLF仅换行差异，首次收尾原始字节断言失败已纠正为发布器同口径。最终业务状态2576确认/41受理/206未知。文档校验23个新增链接零缺失、catalog唯一、XML计数一致，exit0；证据见任务README。

首个新流程完整日批、持续限流后的真实收尾、07:30日报SLA尚待下一轮；平台接口总频率阈值和NAT共享系统待平台/网络确认。不能承诺零限流。旧41平台3/51尚未本地回写，206仍UNKNOWN且人工核查为NOT_FOUND，先核人工历史再制定恢复清单，未重发、未重置原12轮。管理电脑TLS/IP SAN、生产浏览器、分页等原待办不变。

## 下一AI

先读共享STATE本任务最新项和README，确认当前发行、timer和业务日报；不要重跑build/upgrade生成器覆盖不可变包，不要重复创建定时任务。查平台须加载同一生产env共享限速；等待提示不是成功，超时仍UNKNOWN。若需要回退，必须内网发行和宿主driver成对恢复，DMZ恢复旧current；保留新回执，不倒灌旧业务库。下一批若失败先留脱敏sub_code/sub_msg/request_id、job状态及等待时间，再定修复范围。
