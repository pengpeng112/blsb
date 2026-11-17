# 前置机端点测试证据（2026-10-28）

来源主机：<frontend-host>3（平台容器跳板，curl 直连）

## 1. GET（连通性）
HTTP 405，body 含"HTTP状态 405 - 方法不允许 / 此URL不支持Http方法GET / Hclient"，8ms。

## 2. POST {}（空报文）
{"result":false,"errorName":"数据校验错误","errorCode":"04","id":null,"desc":"主键ID必填！,患者ID必填,诊断活动类型代码必填,诊断活动类型名称必填,就诊流水号必填,就诊者姓名必填,身份证件类别代码必填,身份证件类别名称必填,身份证件号必填,医疗机构代码必填,医疗机构名称必填,科室代码必填,科室名称必填,操作时间必填"}

## 3. POST 合成完整记录（见 payload-synthetic.json，假患者+TEST前缀ID+校验位合法假身份证）
{"result":false,"errorName":"数据校验错误","errorCode":"04","id":"TEST-CONN-20261028-001","desc":"医疗机构编码不属于配置范围内！"}

## 端口探测（<frontend-host>，自 <relay-jump>）
80/443/8880/8080/12306 关闭；8881/8882/9000 开放；ping 0.25ms。
