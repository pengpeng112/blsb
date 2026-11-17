# 药品追溯界面优化

本轮按“先看结果，再查问题，配置单独放”的方式整理现有页面。已于2026-10-26 14:50:17完成生产上线，发行ui-clarity-20261026T063000Z。入口仍为https://<prod-host>/ui/#/drugtrace，用户名admin及原密码不变；Ctrl+F5刷新。

## 页面怎么用

| 入口 | 查看内容 |
|---|---|
| 总览（默认） | 四张状态卡、当前进度、每日趋势、需关注事项、计划上报时间 |
| 上报记录 | 单据/平台编号、中文状态、最近反馈、查询结果；下方任务日报及错误详情 |
| 单据与追溯码 | 药房和业务筛选、原始单据及追溯码；采集工具折叠 |
| 对账核查 | 平台与本地差异、已有处置；保留原角色权限 |
| 接入与授权 | 低频接入配置、正式环境不展示演练证据登记；管理员停发需再次确认 |

四张卡分别是“已确认成功、平台已受理、结果待核查、退回/部分成功”。点击卡片进入相应记录筛选。正式模式手工发送保留服务端原授权范围，页面额外确认；取消发送或取消停发不产生POST。

默认近14天，可选择7/14/30/90天；每60秒刷新，可关闭，切到后台时不刷新。字号、间距、颜色层次、窄屏布局及表格滚动已调整。低频配置、原始JSON不占用默认总览空间。

## 数据口径及边界

- 汇总为本租户全部drug_trace profile的**提交记录数**，不是唯一HIS单据数。ACCEPTED不计入最终成功，UNKNOWN不建议重发。
- 日趋势按提交创建日期UTC；任务日报按业务日期北京时间，二者不能按同日直接相减。当前状态流程是状态快照，并非累计漏斗。
- 日报发送数是当次调用数量；已提交而跳过另列，不把重跑日报数字当作整日总量。同日timer/manual详情按点击来源选择。
- “下次06:10”由现行固定计划推算，标注北京时间；并非systemd状态探针。日报为历史执行记录，不冒充正在运行批次的实时状态。
- 接口失败显示“—/暂不可用”，不显示虚假零异常。未登记日报也不称系统正常。
- 现有明细API最多近期200笔，且only=problem在限量之后过滤；本轮没有改后端。汇总有问题而近期列表空会警示“未完整覆盖”，**空列表不等于异常清零**。搜索仅针对已加载明细。
- 当前批准配额、字典revision、证据、历史payload和源视图完全不变；此次UI验证不调用HIS/平台、不新增真实发送/查询。

## 代码和验证

源码在implementation/apps/admin_web/src/views/DrugTrace.vue、drugTrace.css、drugTraceDisplay.ts。基线保留于baselines/DrugTrace.vue；apply_redesign.py由基线及page-template.txt/page-script-extra.txt生成页面，未来开发可直接维护最终源码，本次重跑生成器前需同步模板。

执行命令（工作区根目录；已有依赖、无安装下载）：

```powershell
$env:PYTHONUTF8='1'
python docs/operations/drug-trace-ui-clarity-20261026/apply_redesign.py
$env:VITE_CORE_DEFAULT=''
$env:VITE_STORAGE_DEFAULT='/storage'
npm --prefix implementation/apps/admin_web run build
python docs/operations/drug-trace-ui-clarity-20261026/browser_review.py
node --experimental-strip-types --test implementation/apps/admin_web/tests/drugTraceDisplay.test.mjs
```

实测：vue-tsc+Vite构建exit0；展示辅助测试5通过0失败；浏览器13项通过，涵盖状态、筛选、同日不同来源日报详情、1000/720px无整页溢出、统计失败、空数据、明细不完整、auditor无写入口、停发/发送取消不写。mutation_requests=0、hospital_or_platform_connections=0。证据见[evidence/browser-review.json](evidence/browser-review.json)、[测试TAP](evidence/display-tests.tap)。新增日报测试初次使用即时可见断言早于异步返回，改为等待目标内容后通过；页面未因此更改。

界面截图[桌面](evidence/overview-desktop.png)、[较窄桌面](evidence/overview-compact.png)、[窄屏](evidence/overview-narrow.png)均有**合成数据水印**，不代表生产业务数或生产浏览器验收。

生产6个只读GET接口实际200、字段结构核对通过。初次核验脚本错误假设readiness返回list，按既有profiles对象契约修正后复验通过，未改API。首次离线构建因本机Docker未启动退出，启动既有Docker后成功；失败日志保留，不冒充一次通过。

## 发布与回退

离线包ui-clarity-20261026T063000Z基于auth-20261026T043500Z；manifest差异严格限定四个前端文件，pip freeze一致，无后端/依赖/数据库迁移/部署配置变化，白名单无运行库和凭据。build_release.py为离线构建；transfer_release.py使用既有固定主机指纹通道传输，目标哈希校验通过。

升级前必须等现有mapping-recovery-auth补报自然结束，并核验final为DRIVER_WINDOWS_COMPLETED、两个窗口OK、各驱动inactive、在途发送和待执行job均0。仅升级期间暂停每日timer，标准backup→install完成后一律恢复timer。不得为界面变更重发旧单、重置额度、修改断点或重跑补发launcher。

远程私有日志路径/root/blyb-maintenance-backups/upgrade-ui-clarity-20261026T063000Z/，包含维护前baseline.json、upgrade.log、result.json和ui-verified.json。实际生产结果另附本目录evidence/app-upgrade_status-*.json及app-verify-*.json；未出现成功证据前不能称已上线。

回退走现有rollback.sh auth-20261026T043500Z，先确认无正在运行批次/在途业务；这次前端变化无迁移，不能用恢复旧数据库覆盖新业务回执。账号、用户密码和原服务配置保持。

服务器页面和静态资产验证采用现有证书显式信任；管理电脑到443的TLS问题、证书缺IP SAN仍是既有事项，SSH明确禁止转发，未绕过，HTTP80未启用。**本机合成浏览器通过不等于生产浏览器已验收。**

## 尚待完善

旧41受理/206未知需平台核对、明细后端分页及先筛后限、生产管理入口证书/访问、日报与一次性补发进度的合并呈现，仍待后续独立任务。本轮只改界面并如实标明边界。


## 实际上线结果

[使用说明与证据](README.md)：生产ui-clarity-20261026T063000Z，默认四状态卡、进度、趋势和需关注事项；分总览/上报记录/单据与追溯码/对账核查/接入与授权五入口。英文状态改中文，技术信息折叠；受理与最终成功分开，失败统计不显示虚假零，最多200笔明细边界明确提示。
前端4文件变更，后端/依赖/数据库迁移不变；构建exit0、辅助测试5通过、合成浏览器13项通过且零业务POST。生产六容器healthy、UI与资产200且SHA一致、原授权scope200、timer已恢复active/waiting；服务器验证成功，当前管理电脑TLS/正式IP SAN和生产浏览器验收仍未解决，未启用HTTP或绕过SSH转发策略。
本轮等原批准补发自然结束后才升级，未中断/重跑driver。两个窗口均OK，结束汇总以final.json为准；当前{'CONFIRMED': 2576, 'ACCEPTED': 41, 'UNKNOWN': 206}，used=2823/80000。旧41受理/206未知摘要、源断点46与升级间额度均不变；旧247仍须平台核对，未因界面上线变成已处理。
一致性备份<prod-path>/backups/20261026T064714Z/；私有维护日志/root/blyb-maintenance-backups/upgrade-ui-clarity-20261026T063000Z/。回退上一auth发行，不恢复旧业务库。访问仍https://<prod-host>/ui/#/drugtrace，admin及原独立密码不变，Ctrl+F5刷新。[交接](../../ai/sessions/DTR-UI-CLARITY-20261026.md)。


实测证据：[维护结果](evidence/app-upgrade_status-20261026T064924782615Z.json)、[生产验证](evidence/app-verify-20261026T064952547953Z.json)、[补发及最终状态](evidence/app-status-20261026T064958233581Z.json)。
