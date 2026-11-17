# DTR-HISTORY-COMPARE-20261023

2026-10-23；Codex / GPT-6；session 20261023T010419Z-cf2e5f6f。

用户要求核对指定历史Excel与视图是否一致。离线全量读取/统计、受限差异工作簿和只读SQL计划已完成；HIS实时比对尚未执行，不能判定一致。当前白天窗口待用户确认，已通过异步问题询问，未收到时不执行。

- 范围：docs/development/drug-trace-history-compare-20261023、docs/ai、两份业务README及仓库外本地受限工作区；不修改implementation产品代码、原始历史文件或生产。
- 实测：25文件489613行；205334单号、473614码、81日。格式/精度完整；31次同单同码再次出现、14单号多时间、3562码跨单。全部为待业务核对现象。
- 工作簿：%LOCALAPPDATA%/BLYB/history-compare-20261023/历史上报数据_离线核对.xlsx；六页签，包含原文件/行号，字符串文本保留；同目录history.sqlite3。当前用户/SYSTEM受限ACL，不在Git内。
- 验证：25原件SHA256回查相同；工作簿回读行数匹配；probe_code.sql通过validate_his_sql，SQL未执行。无源库连接、凭据读取、平台调用或真实上传。
- 口径：不能直接对历史单号与新主单号，也不能以历史扫码时间限定发药时间后判缺失。先按source_line_id候选匹配3个实际旧键，再确定真实时间与分批范围。
- 下一步：用户确认当次白天live窗口后，复用sjzc/<relay-jump>容器OracleConnector与既有专用reader安全注入；核对注册身份/只读授权，限定两视图。不重用旧窗口、不读取托管凭据、不扩大到源表。单次至多10000行/30秒，总预算先10分钟；超过预算或截断保留未核范围。
- 不将离线完成标为整体任务完成；真实比对仍待执行。保留原来的1单4码成功，不补报/重发。

详情：../development/drug-trace-history-compare-20261023/README.md及COMPARISON_PLAN.md。
