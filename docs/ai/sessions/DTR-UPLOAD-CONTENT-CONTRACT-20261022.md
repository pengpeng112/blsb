# DTR-UPLOAD-CONTENT-CONTRACT-20261022

- 工具/模型：Codex / unknown。
- 会话：20261022T085436Z-3ef467f5。
- 用户要求：结合F:/python/数据资产中HIS结构及原MED_TRACE_RECORD SQL，尽量确定实际上传内容。
- 范围：本轮分析/SQL草案/证据、docs/ai和对应本机私有基线；未修改业务源码或数据资产仓库文件。
- 凭据沿用既有本机DPAPI/服务器标准受控解析，不向用户重索、不打印内容。

## 已确定

1. 原需求单明确uploadretail；官方快照五必填为bill_code/bill_time/bill_type/ref_user_id/trace_codes。
   药名/码级quantity/unit/患者证件/供应商并非这条最小零售接口必填，不继续把它们列为缺项。
2. 主单采用完整PRESC_DATE/PRESC_NO/DISPENSARY；码来源T_TRACE_CODE，主码三字段JOIN保留。
   原SQL按启用主键RECORD_ID分组，因此MAX不会必然丢其他行码；PRESC_DETAILID是明细号，CREATE_TIME是创建时间。
3. 完整读取两个相关技能并按sjzc/查询/关系流程检索现平台资产：42字段，目标查询0/正式关系0/配方0/值域0。
   源端资产catalog为2026-07-03且无manifest；历史设计文档与现库不完全一致，未冒充实时全量。
4. 本轮通过sjzc追加5条受限SELECT全部成功：16条源注释DUMP按GBK解码、100头/20父键子表样本、r10两视图零行SELECT解析。
   现注释明确SOURCE0门诊/1住院、FLAG1正常/2全退/3部分退、REFUND1已退费/0或NULL未退费。
   今日100头样本41门诊0/FLAG1、59住院1/FLAG0；20码样本13未拆零/7拆零、全部20位码和IS_DELETED0。
5. 已形成正常门诊候选谓词：SOURCE0/FLAG1/发药时间非空/NVL(REFUND_FLAG,0)=0。
   r10仅该分支给正常/完成，其余UNKNOWN/NULL；尚未人工批准/执行DDL，不改APPROVED_STATUS_RULES。
6. 现场只读字典实证新缺项：已批准字典源code只有PATIENT_DISPENSE，但payload优先取原始source_bill_kind_raw，
   必须新增revision补PRESC_SOURCE=0;FLAG=1→321/A并补实际药房→本院ref_ent_id。医院资料已提供不重索。
7. 交付机器契约JSON、r10两视图DDL草案、实测视图回退参考、原SQL和保留条件的记录级化简版。
   仅生成可审查结果，HIS DDL/DML0、平台治理/字典/来源写0、现场配置改0、真实上传0。

## 命令与证据

- ai_session start退出0，无进行中冲突，保存共享文档修改前私有基线。
- sjzc元数据SQL均成功，platform-assets-r1.json；平台查询流程只检索，不伪造run/query版本执行refs。
- 关系CLI首次KeyError('from_columns')，旧HIS CSV列名不兼容；核对平台与旧CSV均无目标关系后，
  用extract函数{}和人工补全函数包装JOIN，original_user-intake-r2/join候选，不写正式资产。
- `build_contract.py`退出0，只生成草案。
- 数据资产backend Python `collect_content_evidence.py r1`退出0，3条SELECT成功；
  `collect_content_evidence.py r1 parse`退出0，两条WHERE1=0解析成功，未CREATE/REPLACE。
- <prod-host>字典首轮SSH255、worker未运行；保留r1失败，r2 SSH0/worker0成功；仅SELECT字典，未调用上传。
- 本地SQL/候选分类反例/契约/链接检查见local-validation.json；不是业务套件重跑或现网升级证明。
- 网页工具本次未取得官方API正文/语雀正文，仍以已有官方快照为参数依据，未声称在线澄清321/323。

## 接续

详见[上传内容确定单](../../development/drug-trace-content-contract-20261022/README.md)。
技术字段来源已确定；需要院方确认首次正常门诊完成/扫码齐套口径及一张未报单。
不要自动推进住院FLAG0、退药/拆零/全历史补报；TRACE_STATUS无注释且为空，不作为未报证据。
确认后执行r10、正常发布来源/字典与药房映射，构建部署之前已修复的Oracle分页新发行，
按真实证据/单据范围/DMZ必要方法/max_bills=1进行首张正式链路闭环。
