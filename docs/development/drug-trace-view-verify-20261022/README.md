# HIS已建视图与账号核验（2026-10-22）

**实际登录成功，两个视图VALID、权限正确、能读到真实追溯记录；当前不能上传。**
执行：Codex / unknown，2026-10-22 16:14 CST起。
用户当前明确授权：保存新账号和视图、即时只读分析；无问题后测试上报1张。
实际核查发现阻断，因此上传条件未满足，真实上传0，不伪造完成状态或放宽质量规则。

## 当前实际连接与本地记录

| 项目 | 当前实测/保存值 |
|---|---|
| Oracle地址/服务 | `<his-host>:1521/his`，实际认证成功 |
| 程序账号 | `BLYB_DRUGTRACE` |
| 账号密码 | 已按用户提供值保存于本机Windows DPAPI凭据文件，见下方；不进入Git和共享文档 |
| 单据视图 | `PHARMACY.V_DRUG_TRACE_BILL`，17列，VALID |
| 码明细视图 | `PHARMACY.V_DRUG_TRACE_CODE`，18列，VALID |
| 账号私有同义词 | `V_DRUG_TRACE_BILL` / `V_DRUG_TRACE_CODE` 均指向上述PHARMACY视图 |
| 权限 | SESSION_PRIVS仅CREATE SESSION；SESSION_ROLES为空；直接对象权限仅两视图SELECT、不可转授权 |

**当前真实owner是PHARMACY，覆盖此前r8执行包中BLYB_RO owner的拟定位置。**
本次不新建BLYB_RO，不要再次运行旧建账号/建视图脚本。现有原件和历史证据保留。

本机受限目录：`F:/python/blyb/.ai/private/drug-trace-view-verify-20261022/`。
其中`connection.json`登记地址/服务/用户/视图/凭据文件指针，
`his-reader.credential.clixml`保存加密账号密码；仅当前Windows用户和SYSTEM可访问，已确认Git忽略。
后续同机AI无需重新向用户索取密码，先读本说明，在进程内载入：

```powershell
$readerCredential = Import-Clixml -LiteralPath 'F:\python\blyb\.ai\private\drug-trace-view-verify-20261022\his-reader.credential.clixml'
# 只在必要连接参数中使用 GetNetworkCredential().Password，禁止输出到终端或日志。
```

DPAPI文件只适合同一Windows用户/机器解密；不能把它直接当Linux密码文件。
当前仅完成本机保存和只读验证，**未将凭据部署到<prod-host>、未写平台源登记、未启用采集**。

## 实际只读数据核查

证据：[reader-r1.json](evidence/reader-r1.json)。通过<relay-jump> `data-asset-api` 受控
`OracleConnector.execute_readonly`，复用平台注册端点与连接方式，使用本次用户新提供的专用账号。
没有读取既有服务器凭据内容，没有改变HIS和源登记，没有使用高权限账号代替新账号验收。
平台注册原源仍为medical_dict_push；本次即时限定核查不将其改写为生产readonly资格。

时间范围：2026-10-22 `[00:00:00, 2026-10-23 00:00:00)`，样本不排序、非随机代表性抽样。
每次调用45秒超时，日样本限20张主单/100条码；一张候选整单限1000+1行保护、实际4行。
没有查询姓名/证件/电话等患者字段；完整单据键和原始码仅在远端进程内匹配，返回本机前已汇总。
没有把ROWNUM限制解释为绝不扫描更多行，没有执行全表COUNT。

| 核查 | 真实结果 | 能证明什么 |
|---|---|---|
| 新账号认证/权限/同义词 | 成功；仅登录系统权限、两视图SELECT | 当前账号能按约定视图名只读使用；没有写入试验 |
| 字段/视图状态 | 17+18列、两视图VALID、完整投影零行解析成功 | 字段结构兼容当前模板，不代表业务口径批准 |
| 主单样本 | 20张，样本复合键无重复；15张原始`PRESC_SOURCE=0;FLAG=1`，5张`PRESC_SOURCE=1;FLAG=0` | 状态组合真实存在；不自行将数字解释为已完成 |
| 码样本 | 100行全部20位数字串，trace_code与t_trace_code一致，记录ID/明细ID/码无空值，IS_DELETED原值均0 | 本样本码形状和原值保留正常；不证明码在平台有效或此前未报 |
| 一张整单 | 1张主单/4条码，记录ID和追溯码均无重复、主码revision一致、未触发行数保护 | 本单视图关联一致；不证明与全部HIS应报明细完整对账或此前未报 |
| Oracle首屏谓词实测 | 当前谓词读0；NULL安全谓词读4 | 当前程序确定漏读首屏，不是HIS没有数据 |

主单样本20/20的business_kind为UNKNOWN，finalized/is_void/expected_code_count为NULL。
明细100/100的quantity/unit/code_level为NULL；整单4条同样为空。
这些是此前交付的待确认视图中明确写入的常量，不应归咎于用户执行失败或声称源库没有对应字段。

值域复核：[value-domains-r1.json](evidence/value-domains-r1.json)：平台针对相关源表状态列登记0条。
离线值域包生成2026-08-29，已超过7天时效，且未提供本次所需批准规则；不猜测状态映射。
旧字段注释和10-19样本仅作线索，不能据其把住院FLAG=0认定为未完成/已完成。

## 为什么这次不上传

1. **业务类型与完成状态尚未落定**：UNKNOWN与finalized=NULL会被现有质量规则阻断，
   分别为QD-KIND-UNKNOWN、QD-NOT-FINALIZED。需要HIS/药房确认正常发药、退药/撤销、完成口径，
   再以新revision更新视图或批准映射。不能简单将所有行改成正常、finalized=1。
2. **程序首屏SQL确实有缺陷**：部署现场`oracle_first_page_null_safe=false`，
   本次真实4条明细在旧谓词下为0。按[已交付精确修复](../../deployment/his-ddl-clock-20261022/NEXT_AI_SOURCE_FIX.md)
   修复、回归、构建和升级，保留复合键/字符串ID/分页边界，不能绕过采集器手工上传。
3. **现网尚未绑定新只读源**：worker Oracle DSN为空，来源DRAFT无active config，
   医院药房映射草稿items=0，目的地对象DRAFT；真实配置PUBLISHED不等于对象已启用。
4. **转发器目前仅允许状态查询**：DMZ方法白名单只有query.billstatus，未开放uploadretail。
   应先完成上面数据与业务条件，再按首张范围配置上传能力；不批量开放方法。
5. **首张未报范围及现有发送证据仍缺**：用户“核查无问题后发一条”的授权已明确，
   不再重复询问是否同意测试；仍需确定完整单据、正常完成/未撤销与未由其他系统上报。
   不能把随机抓取的4码候选当作院方确认的测试单。按系统正常审批/配额流程记录当前授权，max_bills=1。

quantity/unit/code_level在本样本为空；**数量为可选字段、层级未确认在现质量规则中是警告，
不能把这些一概说成平台必填或单独阻断原因**。涉及拆零时仍须确认每码数量/包装使用规则，
不把TRACES_SUM直接当quantity，也不自动填1。

## 当前服务与平台链路

有效证据：[application-r2.json](evidence/application-r2.json)、[dmz-r1.json](evidence/dmz-r1.json)。

- <prod-host>仍为fb-20261021T164800Z，6容器healthy；实际worker UID10001、签名资格true，
  真实目的地配置PUBLISHED rev1，但对象DRAFT、来源DRAFT/DSN空，业务单据/采集/submission/配额0。
- worker→严格mTLS→DMZ→官方状态查询1次成功，total_num=0；这是随机探针，无真实上传。
- collect/send证据仍MISSING，query/reconcile为APPROVED。证据状态的缺失键不是重复向用户索要已知AppKey或Secret。
- DMZ官方HTTPS HEAD200；时钟16:18:08 CST与同轮官方Date08:18:09 GMT接近，
  先前校时仍有效。NTP仍未同步；测试CA标识仍在，均未在本次变更。
- application-r1曾SSH255且无结构结果，原因未取得；保留失败证据，不能称第一次成功。
  明确新r2复查SSH0/worker0/状态查询成功，未关闭主机校验或更改凭据。

## 下一步执行顺序

1. **无需重建账号、无需重建当前视图。** 按本页实际PHARMACY owner接续。
2. 下一AI按协议登记代码范围，先修Oracle空游标并测试首屏/后续页/前导零/药房隔离，构建新发行；
   当前任务没有改业务源码或升级现网，不能以本报告代替修复通过。
3. HIS/药房确认两个原始状态组合的业务及完成/撤销含义，选定未报测试单；不能仅凭相似注释批准。
4. 针对已确认口径另出revision对齐两视图、业务规则、药房ref_ent_id及bill_type/query_type映射，
   实际核对修改后的整单；历史视图定义已在reader-r1证据中保存。
5. 按正式入口绑定新只读账号、发布/启用来源与目的地，采集测试单至质量VALID，冻结预览并核对码数。
6. 用已提供的平台凭据继续，不重复索要；落实uploadretail权限/321或323冲突、现网证据、证书/校时策略及单据未报范围。
7. DMZ仅新增必要uploadretail方法，签发该单max_bills=1额度；正常系统流程发送1张，保留结果、查终态、逐码核对。
   HTTP200或请求受理不能写成最终处理成功，UNKNOWN先查不重发。

## 本轮命令与状态

```powershell
& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-view-verify-20261022/verify_reader.py' r1
# 退出0：新账号认证及限量核验成功，16项SELECT调用均成功。

& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-view-verify-20261022/recheck_runtime.py' application r2
# 退出0：SSH0、worker0、平台状态查询成功。

& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-view-verify-20261022/recheck_runtime.py' dmz
# 退出0：只读状态与官方HEAD取证成功。
```

回执使用独占创建，原命令不能覆盖旧证据；需重复核验先新增revision并明确范围，不盲目重跑。
本地校验见[local-validation-r3.json](evidence/local-validation-r3.json)。首轮仅折叠空白比较SQL文本，
将逗号旁空格差异误判为不同，原失败留在local-validation.json；r2按完整SQL token（保留字面量）
比较，确认实际两视图SELECT与交付内容一致；r2另因报告尚未生成导致自身链接检查失败，
r3先独占预留输出文件后核对链接，通过全部检查。原失败均保留，未修改活库视图来通过检查。
实测通过：账号/视图/限量样本、首屏缺陷证明、服务状态及平台只读查询。
未完成：业务口径、取数修复与升级、新来源绑定/采集/首张发送；真实上传0、HIS写0、远端配置修改0。
