# DTR-ORACLE-FIRST-PAGE-20261022

- 工具/模型：Codex / unknown。
- 会话：20261022T083228Z-c5704879。
- 用户最新指令：“继续”，承接只读核验和符合条件后首张上报目标。
- 实际范围：Oracle码明细模板、新增回归测试、本轮受限SELECT探针、四类实施文档和共享状态。
- 基线：开工时已存在其他AI修改；已保存目标源文件/共享文档/实施文档至本任务受限私有目录，未reset/clean。

## 已修改

模板仅增加首次NULL游标分支，后续字符串严格递增；复合键/排序/行上限不变。
新增12项回归测试，在独立临时SQLite SQL引擎执行真实谓词、空串绑定按Oracle NULL适配；
保留前导零/空单/满页/短页/四个复合键隔离。未降低业务类型、完成状态和发送质量门禁。
按要求同步四类实施文档。独立patch对比开工备份，不混入原有未提交修改。

## 真实命令/结果

- 修复前新测试：`python -m pytest tests/integration/test_drug_trace_oracle_first_page.py -q --junitxml=.../before.xml`，
  退出1，10失败/2通过，复现原缺陷。
- 首轮6文件局部：退出1，107通过/1失败/2跳过；唯一失败为既有测试命中System32 WSL bash，/bin/bash缺失。
- 使用已安装D盘Git Bash/OpenSSL，仅调整本进程PATH并恢复；同6文件复验退出0，**110通过/0失败/0跳过**，
  41.14秒，targeted-r2.xml。未安装依赖，未改既有测试断言。
- 数据资产backend Python运行`live_verify.py r1`，退出0；新专用账号经<relay-jump>标准OracleConnector.execute_readonly，
  渲染本地修复SQL并增加同日守卫，实测1单4码、2/2/0分页，空串/NULL首屏和整体顺序/键隔离/revision均通过。
  只回传汇总，无原始码/单据键/患者字段，源写0/源登记变更0/远端配置变更0/上传0。
- 新发行未构建、未升级<prod-host>；未改任何现网不可变发行文件，不声明部署通过。

## 剩余

现网仍需新发行；视图业务kind UNKNOWN/finalized空，仍待院方口径，禁止硬填正常/已完成。
新账号/PHARMACY两视图和密码已本地保存并实测，无需重建/重索取。
来源/DSN、医院药房映射、目的地启用、真实证据/方法白名单/一张未报范围尚未实施。
用户已授权符合条件后测1张；当前条件未满足，实际上传0。已向用户询问具体状态口径和未报单据。

接续入口：[本轮修复报告](../../development/drug-trace-oracle-first-page-20261022/README.md)。
