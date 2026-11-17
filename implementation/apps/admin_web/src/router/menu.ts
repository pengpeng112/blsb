// LT-02 菜单集中配置（纯数据数组；新增一项只加一行，LT-06 直接往里加）。
// 分组与角色照 LT-02 任务书表格；路径保持旧路径不变（书签兼容）。
// RT-01：view 显式指定组件名（路径≠组件名时必填，杜绝 undefined 白屏）；
// targetScope 声明业务范围（受限账号按 target_scopes 过滤侧栏入口）。
import type { MenuGroup } from '../utils/menu'

export const MENU: MenuGroup[] = [
  { key: 'workspace', label: '工作台', items: [
    { path: '/dashboard', label: '上报驾驶舱' },
    { path: '/overview', label: '平台运行指标' },
  ] },
  { key: 'business', label: '上报业务', items: [
    { path: '/targets', label: '上报业务目录' },
    // RT-01：/targets/* 由动态路由 /targets/:code 接管（路由层跳过静态注册），
    // 菜单项只作入口；targetScope 限定可见账号。
    // PT-03：直达传染病专属监控座舱（五页签；不再落到通用业务详情）
    { path: '/infectious', label: '传染病上报', view: 'infectious',
      targetScope: 'inf_cdc_frontend' },
    { path: '/drugtrace', label: '码上放心',
      targetScope: 'drug_trace_alihealth' },
    { path: '/cases', label: '病理工作台', mock: true },
  ] },
  { key: 'ops', label: '运行监控', items: [
    { path: '/ledger', label: '提交与回执' },
    { path: '/schedules', label: '调度任务' },
    { path: '/alerts', label: '告警中心' },
    { path: '/submissions', label: '发布与上报', mock: true },
    { path: '/publications', label: '视图发布' },
    { path: '/reconciliations', label: '对账' },
  ] },
  { key: 'governance', label: '数据治理', items: [
    { path: '/quality', label: '质量队列' },
    { path: '/review', label: '关联复核' },
    { path: '/blockers', label: '外部阻断' },
  ] },
  { key: 'integration', label: '接入配置', items: [
    { path: '/connectors', label: '连接器', roles: ['platform_admin', 'config_editor'] },
    { path: '/mapping', label: '字段映射', roles: ['platform_admin', 'config_editor'] },
    { path: '/lineage', label: '字段血缘', roles: ['platform_admin', 'config_editor'] },
    { path: '/workflows', label: '流程编排', roles: ['platform_admin', 'config_editor'] },
    { path: '/pack', label: '医院配置包', roles: ['platform_admin', 'config_editor'] },
    { path: '/metrics', label: '指标导入', mock: true,
      roles: ['platform_admin', 'config_editor'] },
  ] },
  { key: 'assets', label: '资产与设备', items: [
    { path: '/devices', label: '设备采集', roles: ['platform_admin', 'operator'] },
    { path: '/files', label: '文件资产', roles: ['platform_admin', 'operator'] },
    { path: '/storage', label: '存储管理', roles: ['platform_admin', 'operator'] },
  ] },
  { key: 'system', label: '系统', items: [
    { path: '/system/users', label: '用户与角色', view: 'users',
      roles: ['platform_admin'] },
    // RT-01：审计检索升级版（AuditSearch.vue）占用正位路径 /audit；
    // /audit2 在路由层重定向到 /audit 兼容旧书签，侧栏不再出现双审计入口。
    { path: '/audit', label: '审计检索', view: 'auditSearch',
      roles: ['platform_admin', 'auditor'] },
  ] },
]
