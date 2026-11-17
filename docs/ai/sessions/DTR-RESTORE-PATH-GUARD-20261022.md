# DTR-RESTORE-PATH-GUARD-20261022

Codex / unknown；session 20261022T095749Z-17c7fc32。顺序处理现场发现的恢复路径保护缺陷，无子代理。
范围：implementation/deploy/linux/restore.sh；相关新增测试/发行/文档由同一工具的主任务DTR-SOURCE-DELIVERY登记范围承接。

旧脚本 [ "$rt" = "$rp"/* ] 会展开已有目录，导致too many arguments并继续返回校验成功；现场旧预检已复现。
改为case前缀匹配，不做文件名展开；新增相等/祖先/子孙三个真实目录反例拒绝。
Windows首次测试受GBK/UTF8编码影响，进程PYTHONUTF8=1后整个运维文件16 passed/退出0，未弱化断言。
修复随src-20261022T103000Z已部署；服务器restore.sh SHA256=44cbb5b31e1976c873ed16cd8eb014aa6bf9adaadd1359bb7baad7efc8d846f1。
在新独立根<prod-path>-restore-check-20261022对备份20261022T095541Z运行--validate-only成功，无旧警告；未实际恢复生产库。
旧默认演练目录在生产根下会被正确阻断，运维需显式给独立data-root，已记录到主任务README和共享BACKLOG。

[主任务及证据](../../development/drug-trace-source-deploy-20261022/README.md)。本缺陷完成，平台真实发送仍按主任务外部条件阻断。
