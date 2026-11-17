# medins_emr_sd 契约提取报告（LT-07，2026-11-11）

提取器：`tools/spec_extract` v1.0；原件 sha256 见 `profile.json`。
**本报告与全部产物不含样例值**（结构/规则/合成值 only）。

## 覆盖率

| 指标 | 数 |
|---|---|
| 规范接口清单表交易数 | 19（11101—11103/11301/11401—11408/12101—12104/12201/12401/12501） |
| 标题归属追加交易 | 4（12601—12604，不在清单表但在正文标题） |
| 成功提取字段契约的交易 | 23/23（清单 19 + 1260x 4，无缺） |
| 样例文件 | 23（调用机构医保 19 + 12601-12604 目录 4） |
| 样例结构成功解析 | 23/23（含 GBK 编码文件） |
| 无法归属的参数表 | 0 |
| 字典表提取 | 1（opter_type，规范报文头表第 11 行明示） |

## 规范要点（结构 only）

- **公共报文头**（fields/_envelope.json，17 字段）：info/msgid/mdtrtarea_admvs/
  insuplc_admdvs(传00000000)/recer_sys_code/dev_no/dev_safe_info/cainfo/signtype/
  infver/opter_type/opter/opter_name/inf_time/fixmedins_code/sign_no/input。
- **msgid 规则（规范已证实）**：定点医药机构编号(12)+时间(14,yyyyMMddHHmmss)+
  顺序号(4)=30 字符，不允许重复（报文头表 msgid 行说明原文）→ 已实现
  `build_msgid` 并测试长度/唯一。
- **返回结构**：infcode(数值)/inf_refmsgid/refmsg_time/err_msg/output；
  成功判定 infcode=0（STATUS 表：0 成功/-1 失败）——Mock 据此合成。
- **传输/签名**：规范定义 cainfo(1024)/signtype(10) 字段，**未给出算法、密钥
  形态、测试向量，也未说明 HTTP 头（x-tif-*）要求** → SIGNATURE_UNCONFIRMED，
  导出包签名头留占位。

## 人工待核项（UNCONFIRMED 清单）

1. **签名算法**（正式发送阻断）：需院方/医保提供算法说明+向量。
2. **长度单位**：所有 max_len 未说明字符/字节 → LENGTH_UNIT_UNCONFIRMED
   （校验为字符演示口径）。
3. **info 长度矛盾**：报文头表标注 4，但交易编号全为 5 位——envelope 契约
   info.max_len 置 None 并留 notes，待医保确认。
4. **12601—12604 不在接口清单表**（仅正文标题）——direction 已按 upload 记，
  与清单关系待核。
5. 嵌套结构层级：规范表为平铺字段，`input` 下子对象/数组结构以样例 shape
   标注（source=sample_only 的深层字段仅 WARN 提示，不阻断）。

## 再生成

原件更新后重跑（派生物，不改旧目录语义——建议新目录或升 spec_version）：

```
python -m tools.spec_extract.build_profile --target medins_emr_sd \
  --spec "<规范docx>" --samples "<样例目录1>" --samples "<样例目录2>" \
  --out profiles/medins_emr_sd
```
