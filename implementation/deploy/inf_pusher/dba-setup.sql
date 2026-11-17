-- DBA 执行：传染病上报推送器专用 ODS 只读账号（30 秒）
-- 在 ODS <ods-host>:1521/orcl 上以具备 DBA/属主权限的账号执行；密码请自定并同步写入 <prod-host> 的 inf.env
CREATE USER INF_PUSHER IDENTIFIED BY "<自定密码>";
GRANT CREATE SESSION TO INF_PUSHER;
GRANT SELECT ON ODS.V_INF_ADMISSION_RECORD   TO INF_PUSHER;
GRANT SELECT ON ODS.V_INF_FIRST_COURSE       TO INF_PUSHER;
GRANT SELECT ON ODS.V_INF_DAILY_COURSE       TO INF_PUSHER;
GRANT SELECT ON ODS.V_INF_DEATH_INFO         TO INF_PUSHER;
GRANT SELECT ON ODS.V_INF_EX_CLINICAL        TO INF_PUSHER;
GRANT SELECT ON ODS.V_INF_EX_CLINICAL_ITEM   TO INF_PUSHER;

-- 然后在 <prod-host> 上写入凭据文件（宿主机，root 执行）：
--   cat > <prod-path>/exchange/inf-pusher/inf.env <<'EOF'
--   INF_ODS_USER=INF_PUSHER
--   INF_ODS_PASSWORD=<同上密码>
--   INF_ODS_DSN=<ods-host>:1521/orcl
--   EOF
--   chown 10001:10001 <prod-path>/exchange/inf-pusher/inf.env
--   chmod 600 <prod-path>/exchange/inf-pusher/inf.env
