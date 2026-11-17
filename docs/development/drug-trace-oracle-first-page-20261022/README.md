# Oracle追溯码首屏修复（2026-10-22）

**本地代码已修复，相关回归110通过/0失败/0跳过，真实Oracle限量SELECT验证成功。**
尚未构建/部署新发行，<prod-host>仍为旧模板；真实上传0。
本轮承接用户“继续”，由Codex / unknown顺序执行，不启用并行代理。

## 修了什么

`implementation/plugins/sources/db_view/drug_trace.py`的码明细模板首次收到`after_record_id=''`，
Oracle会按NULL处理，原`source_code_record_id > :after_record_id`始终不匹配。
本次仅把该条件改为：

```sql
AND (:after_record_id IS NULL
     OR source_code_record_id > :after_record_id)
```

首次空值允许取数，后续仍按原始字符串ID严格递增。复合键、ORDER BY、ROWNUM上限、
业务/完成状态、质量门禁和批准字典不变。当前工作树已有其他AI未提交成果，本次以开工时文件备份为基线，
增量仅见[独立补丁](oracle-first-page.patch)，未用相对Git HEAD的大差异冒充本次修改。
SHA见[change-summary.json](evidence/change-summary.json)。补丁对应修改已在本地落地，不要重复套用。

## 怎么验证

| 验证 | 结果 | 证据 |
|---|---|---|
| 新增回归在修复前执行 | 12项中10失败/2通过，复现首屏空结果及整单漏码 | [before.xml](evidence/before.xml) |
| 首轮相关回归 | 107通过/1失败/2跳过；唯一失败为误用WSL bash，系统无/bin/bash | [targeted.xml](evidence/targeted.xml) |
| 配置进程PATH使用已有Git Bash/OpenSSL后复验 | **110通过/0失败/0跳过**，没有修改断言或安装依赖 | [targeted-r2.xml](evidence/targeted-r2.xml) |
| 真实Oracle只读复验 | 新账号认证成功；NULL/空串首屏一致；一单4码分页2/2/0，整单及顺序匹配、无重复、revision一致 | [oracle-live-r1.json](evidence/oracle-live-r1.json) |

新增12项测试使用临时SQLite SQL引擎执行真实渲染谓词，适配器仅将空串绑定为NULL并翻译外层ROWNUM限制，
不以Python过滤替代SQL；覆盖NULL/空串、满页/短页、字符串前导零、四个复合键分别隔离、空单和后续严格游标。
这不是Oracle服务器替身的实测声明。真实Oracle验证另行通过<relay-jump>标准受控连接器，仅SELECT既有视图。

Oracle现场复验使用当前本地模板渲染，附加同日时间守卫；不宣称未加时间守卫的全量性能已验。
候选限制在2026-10-22同日、一张完整复合键单据、最多20条基线和12页保护，实际4条码、3页。
每次45秒超时，未导出原始码/单据键/患者字段，HIS写0、源登记修改0、远端配置修改0、平台上传0。

实际复验命令：

```powershell
# 在implementation目录，PATH仅在本进程使用已安装的Git工具，结束后恢复
$testSavedPath = $env:PATH
try {
  $env:PATH = 'D:\Program Files\Git\bin;D:\Program Files\Git\usr\bin;' + $testSavedPath
  python -m pytest tests/integration/test_drug_trace_oracle_first_page.py tests/integration/test_drug_trace_domain.py tests/integration/test_drug_trace_fix2.py tests/integration/test_drug_trace_fix3.py tests/integration/test_fin_finalize.py tests/integration/test_his_source_profile.py -q -ra --junitxml=../docs/development/drug-trace-oracle-first-page-20261022/evidence/targeted-r2.xml
} finally { $env:PATH = $testSavedPath }

# 在仓库根，已经执行；输出独占创建，重复复验需新revision
& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-oracle-first-page-20261022/live_verify.py' r1
```

## 接下来如何上报

账号/密码/视图已保存并核验，见[上一轮完整报告](../drug-trace-view-verify-20261022/README.md)。
实际owner为PHARMACY，程序账号BLYB_DRUGTRACE，密码位于本机DPAPI文件；不要重复建用户或索要平台Secret。

1. **业务口径仍待确认**：真实头样本business_kind全UNKNOWN、finalized为空，现质量规则会等待。
   请HIS/药房确认`PRESC_SOURCE=0;FLAG=1`、`PRESC_SOURCE=1;FLAG=0`分别表示何种发药/退药状态，
   哪些确已完成且未撤销，选一张未被其他系统上报的测试单。不要把“继续”解释为批准任何状态映射。
2. **新发行和来源配置尚未实施**：按现有发行构建/备份/升级流程落地修复，不能改现网不可变发行目录。
   新账号尚未注入worker，来源DRAFT/无DSN，目的地对象DRAFT，药房→医院ref_ent_id映射为空。
   必须逐项正常发布、启用和核验，不能把本机连库成功写成<prod-host>已接好。
3. **首张发送**：将确认的业务/完成口径以新revision对齐视图和映射，整单采集质量VALID、冻结预览；
   落实当前真实发送证据、平台uploadretail能力、DMZ必要方法白名单及首张范围/配额max_bills=1。
   严格链路发送一次，查平台终态并核对全部码；未知先查不重发。

quantity/unit/code_level空值不能笼统说成平台必填；拆零数量与包装规则仍不得猜测。
DMZ时钟前轮已纠正且本轮时点准确，长期NTP未同步和测试证书链仍是原有待项，没有在本次修复中冒称已解决。

四类实施文档已同步追加本轮事实；当前验收分层：本地修复/局部回归/真实受限SELECT通过，
新发行构建与<prod-host>升级未执行，业务确认/来源绑定/首张上传未完成。
