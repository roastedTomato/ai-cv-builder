# MySQL 前置工作记录

## 旧存储清理（2026-11-23）

- 按用户要求，已直接删除本机应用数据目录中的 `base-resume.json` 和 `job-records.json`，不备份、不导入 MySQL。`job-records.lock` 不含业务记录，保留到本地存储切换完成。
- 已在 AWS 账号 `349744180219` 的 `ap-southeast-2` 区域，核对 `cvbuilder-sydney-test` 堆栈中 `RecordsTable` 对应的物理表 `cvbuilder-sydney-test-RecordsTable-O1Y6B9ECT96V`，删除后等待至表不存在。
- 旧云端测试站点的数据接口因此不可用；CloudFormation 模板仍声明该表，堆栈在后续基础设施更新前存在资源漂移。不要在完成 MySQL 云端配置前直接重部署旧模板。

本记录不包含业务数据或数据库凭据。
