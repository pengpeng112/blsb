# 码上放心平台申请与资料交付

AppKey/AppSecret及医院企业标识是后续正式平台调用所需，**当前HIS核查不依赖它们**。
可现在并行申请，不需要等视图开发全部完成。服务器root或HIS密码不能替代这些身份。

## 如何申请

先找医院现有“码上放心”企业账号管理员、药房负责人或对接的平台业务经理，确认是否已有可用应用，
避免重复申请后导致多个应用/企业授权混淆。医院入驻信息、接口服务及有效期一并核对。

码上放心[官方开放平台说明](https://www.mashangfangxin.com/help/openPlatform)
给出的通用流程为：与业务经理确认权限场景→开发者账号/首轮授权→开放平台创建应用→
将AppKey交业务经理完成下一轮授权→在应用中核对可调用API。
该页以连锁药店为例，**不能照选其连锁药店权限包或照抄其API**。
本院应让业务经理确认医疗机构场景及以下两个方法确实授权：

- `alibaba.alihealth.drugtrace.top.yljg.uploadretail`：本期需求确认单指定的发药销售上传。
- `alibaba.alihealth.drugtrace.top.yljg.query.billstatus`：处理状态查询。

AppKey和AppSecret由实际获授权应用管理员从开放平台应用配置取得；
医院ref_ent_id、ent_id由码上放心企业账号/平台对接人员确认，两者分别提供，不要求相同。
若应用由第三方ISV持有，医院未必能取得或应持有对方AppSecret；请对方明确医院授权/绑定及session/token方案。
不要把其他机构或测试示例AppKey填到本院系统。

本地接口地址文件链接的是[医疗机构指南2026.01](https://qg6r2i.yuque.com/qg6r2i/dxne69/gxe667idmsa9b75e?singleDoc=)。
本轮浏览工具未能读取该语雀全文，所以不声称已核实其当前菜单、权限包精确名称或自用/session规则。
如需当前完整办理说明，请管理员从该指南/平台客服确认，不依赖其他AI引用或网上旧教程。
官方[FAQ](https://www.mashangfangxin.com/help/faq)提供在线客服及400-9500-111咨询渠道。

## 可直接转交管理员/平台的文字

> 山东省第二人民医院拟将HIS药房发药销售追溯码对接码上放心。
> 请确认医院企业账号的接口服务、有效期及医疗机构应用申请方式，开通
> alibaba.alihealth.drugtrace.top.yljg.uploadretail与
> alibaba.alihealth.drugtrace.top.yljg.query.billstatus。
> 请提供或协助确认应用AppKey、医院ref_ent_id和ent_id、已批准API/权限包清单、
> 自用或ISV授权模式及是否需要session/token、正式HTTPS网关、调用IP白名单要求和官方SDK。
> AppSecret请通过医院受控配置/安全交付渠道保存，不放在普通聊天、邮件正文或开发文档。

上述为交付用文字，本轮未代发任何消息或提交申请。

## 拿到后如何提供

| 信息 | 给开发方的方式 |
|---|---|
| AppKey、医院ref_ent_id、ent_id、企业名称 | 在院内交接清单注明其归属和环境；它们不是HIS机构码 |
| 权限包/已授权方法、有效期、模式 | 提供文字或遮住密钥的页面截图；应能确认上传和查询分别授权 |
| AppSecret、session/token（如需） | 存医院受限配置文件或既有SecretRef后端；只告知部署人员路径/引用，勿贴AI聊天或提交Git |
| 正式网关、白名单要求、SDK | 提供官方说明/文件位置及版本；不把dmz内网IP当平台企业授权标识 |

建议先由管理员确认应用和权限是否已有。需要本机暂存Secret时使用Git忽略且仅管理员可读的位置，
再由部署流程注入<prod-host>签名服务；不放公共Markdown，不交给DMZ转发器签名。
现有程序支持SecretRef，后续开发方需核对实际可用的引用后端和权限，不能凭空写一个路径就说已配置。

有AppKey不等于上传已获授权；查询方法成功也不等于上传方法、企业绑定和服务有效期全部验证成功。
仍需与平台按批准范围完成签名、接口权限、首批真实发送和业务终态核验。
