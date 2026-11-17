# 契约与示例说明

本目录均为设计交付材料。没有运行中的生产服务，没有厂商接口实现，也没有正式病理医保接口。

| 文件 | 用途与边界 |
|---|---|
| acquisition-manifest.schema.json | 我方Agent提交已确认完成清单的内部Schema |
| acquisition-manifest.example.json | 合成采集事件，模拟厂家manifest；不是迪英加实际报文 |
| transport-fixture.bin | 内容仅为3字节`abc`，与示例size/hash一致；不是病理图像 |
| workflow.schema.json | 通用编排DSL语法草案；节点能力、输入输出和无环性需额外语义检查 |
| pathology-workflow.example.json | 内部Mock病理推送流程；不是生产医保协议 |
| metrics-workflow.example.json | 无患者业务的通用性演示 |
| metrics.schema.json / metrics.example.json / metrics.example.csv | 合成运营指标Schema与导入样例 |
| destination-profile.example.json | PACS参考出口草案，未确认项明确阻断生产 |
| mapping.example.json | 字段转换声明示例；源字段和字典未经医院确认 |
| internal-openapi.yaml | 关键内部API的OpenAPI 3.1草案；完整API目录在04 |
| PACS_V2_字段清单_原样提取.csv / .json | 从本地原件提取288条字段，不做自动纠错；保留中文条件必填、疑似错字及版本冲突 |
| 生成契约样例.py | 重新生成样例的辅助脚本，依赖PyYAML；不属于应用实现 |
| 验证契约样例.py | 设计契约校验脚本；依赖jsonschema、openapi-spec-validator、PyYAML |

所有`.example.invalid`地址均为不能连接的占位地址，禁止改成真实地址后直接当已完成联调。

## 开发必须补充的语义检查

- 清单中的event_id是Agent持久事件ID，client_asset_id只是客户端文件引用，登记响应返回映射后的服务端asset_id。创建上传会话须使用响应asset_id，而非任意猜测ID。
- Schema只验证基本形态，服务端还要验证agent与tenant、来源权限、证据真实性、immutable策略、重复成员、路径安全、清单一致性及配额。
- source_immutable_assertion为false时不能依赖源路径直接传输并宣称稳定快照；需要我方spool快照和经批准完成证据，否则待确认。Schema允许表达false，业务层决定是否可放行。
- workflow参数由每个节点插件的配置Schema继续验证；通用Schema不穷举全部插件字段。
- 工作流的needs_review/invalid输出可以没有普通后继，由运行时转WAITING_HUMAN/INVALID并创建问题项；不能默默当success继续。审批后重评估相关节点，仍不改冻结提交。
- 同一ID或幂等键关联不同内容必须冲突，不能以last-write-wins覆盖。
- 内部时间统一RFC3339；对外yyyy-MM-dd等目标格式只在Profile输出时转换。
- file/asset类型允许传输不代表可解码或医保接受，后两项单独验证。
- OpenAPI中的204分片成功要求校验与持久化登记已完成；202 complete仅代表受理整文件校验。实际实现必须落实Content-Length检查及HTTP层限流/超时，不能只依赖Schema。

## 设计校验与系统验收不同

本包校验仅证明文件可解析、示例满足Schema、OpenAPI结构有效以及若干静态不变量；不证明程序已实现，不证明大文件、硬件、HIS、PIS、扫描软件或医保采集器可用。程序验收必须按07执行。

复跑时在独立Python虚拟环境安装本目录requirements-design.txt，再执行验证契约样例.py；生成样例时运行生成契约样例.py。验证脚本按当前交付文件数记录检查数量。2026-09-30已将临时依赖移至根目录归档/2026-09-30/校验依赖/validation-deps；它们仅用于文档校验，不是应用实现或默认复核材料。

## v0.4新增契约材料

- `first-hospital-pack.example.json`：首院拟议配置，所有生产使能关闭，未批准字典/外键/时间语义显式为空；不是正式完整HospitalPack Schema。
- `first-hospital.synthetic-fixture.json`：合成玻片条码候选，含唯一/空/歧义/前导零，未含真实身份。
- `first-hospital.acceptance-cases.json`：07的F01—F20验收规范，全部NOT_EXECUTED，开发需另写真正测试。
- 主manifest示例只含barcode_raw，不依赖pathology_no；病理工作流resolve后加入语义quality节点，失败/等待禁止继续冻结。
- 10章新增业务API仍需实施时补到完整OpenAPI；已有OpenAPI仅覆盖部分内部链路。

生成契约样例.py只生成原有Schema/工作流/OpenAPI及主清单，不重写上述手工维护的首院材料。更改07验收矩阵时同步验收JSON，并运行静态校验检查编号/文本一致。
