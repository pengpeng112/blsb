-- =============================================================================
-- 【第 1 步】创建 V15 锚点对照视图（纯视图，无表依赖，直接可执行）
-- 请 DBA 将以下整段 SQL 复制到 SQL 工具中执行（逐条或整体均可）
-- =============================================================================

-- 1a) 建锚点对照视图（V_INF_EX_LAB/ITEM 依赖它）
CREATE OR REPLACE FORCE VIEW ODS.M_INF_FXHIS_ACTIVITY_V15 AS
SELECT SERIAL_NUMBER,
       ACTIVITY_TYPE_CODE,
       PATIENT_ID,
       OPERATION_TIME
  FROM ODS.M_INF_FXHIS_ACTIVITY;

-- 1b) 授权（如推送器账号不同则需单独授权，通常 ODS schema 内视图自动继承）
-- GRANT SELECT ON ODS.M_INF_FXHIS_ACTIVITY_V15 TO <推送器ODS账号>;

-- 1c) 验证：应返回门诊+急诊行数 ≈3166
SELECT COUNT(*) AS V15_CNT FROM ODS.M_INF_FXHIS_ACTIVITY_V15;

-- =============================================================================
-- 【第 2 步】确认 M_INF_FXHIS_ACTIVITY 主锚点视图已包含门诊段（DBA 前一轮已建）
-- 验证：应返回 ≈3166
-- SELECT COUNT(*) FROM ODS.M_INF_FXHIS_ACTIVITY WHERE ACTIVITY_TYPE_CODE IN ('1','2');
-- 如为 0，说明主锚点视图的门诊段尚未创建——请先执行 02-上报流程与运维手册.md 中
-- "门诊检验接管"的锚点主视图 DDL（含住院段+门诊段 UNION ALL）。
-- =============================================================================

-- =============================================================================
-- 【第 3 步】执行完后 → 重跑 02 号和 03 号检验视图 → 复核
-- =============================================================================

-- ==============================================================================
-- 以下为完整的 V15 锚点对照视图创建 + 验证 + 依赖重建脚本（DBA 按顺序执行）
-- ==============================================================================
-- 步骤 1：建锚点对照视图
-- CREATE OR REPLACE FORCE VIEW ODS.M_INF_FXHIS_ACTIVITY_V15 AS
-- SELECT SERIAL_NUMBER,
--        ACTIVITY_TYPE_CODE,
--        PATIENT_ID,
--        OPERATION_TIME
--   FROM ODS.M_INF_FXHIS_ACTIVITY;
--
-- 步骤 2：验证锚点数据量
-- SELECT COUNT(*) AS V15_CNT FROM ODS.M_INF_FXHIS_ACTIVITY_V15;
--
-- 步骤 3：验证门诊锚点行数（应 ≈3166）
-- SELECT COUNT(*) FROM ODS.M_INF_FXHIS_ACTIVITY_V15 WHERE ACTIVITY_TYPE_CODE IN ('1','2');
--
-- 步骤 4：重建依赖它的 V_INF_EX_LAB（使其 INVALID 自动恢复 VALID）
-- ALTER VIEW ODS.V_INF_EX_LAB COMPILE;
--
-- 步骤 5：验证状态
-- SELECT OBJECT_NAME, STATUS FROM ALL_OBJECTS
--  WHERE OBJECT_NAME IN ('M_INF_FXHIS_ACTIVITY_V15','V_INF_EX_LAB') ORDER BY OBJECT_NAME;
-- ==============================================================================
