# 本地 MySQL 运行说明

本项目使用 MySQL 8.4 的独立数据库。首次运行前，在本机 MySQL 中创建一个空库和具有该库读写权限的专用账号；不要将密码写入仓库、命令行历史或测试输出。设置下表中的连接环境变量后，按编号应用尚未执行的迁移（可重复运行）：

```sh
.venv/bin/python -m cvbuilder.mysql_migrate
```

本地网页进程从以下环境变量读取连接信息：

| 变量 | 用途 |
| --- | --- |
| `CV_MYSQL_DATABASE` | 数据库名 |
| `CV_MYSQL_USER` | 数据库账号 |
| `CV_MYSQL_PASSWORD` | 数据库密码 |
| `CV_MYSQL_HOST` | TCP 主机；本地默认 `127.0.0.1` |
| `CV_MYSQL_PORT` | TCP 端口；默认 `3306` |
| `CV_MYSQL_SOCKET` | 可选的本机 Unix socket；设置后优先使用它 |

本地网页使用固定的本地用户标识；它仅监听回环地址，不提供多用户登录。未来云端入口必须从已验证的登录身份取得用户 ID。

设置连接环境变量后，再从**同一个终端**运行 `.venv/bin/cvbuilder-web`，打开 `http://127.0.0.1:8000`。项目不会自动读取仓库中的 `.env`。若未设置数据库名或账号、MySQL 未启动，页面读取 API 会返回 `database_unavailable` 并提示检查连接配置。

本机已安装 `/usr/local/mysql` 时，也可运行 `bash scripts/run_local_acceptance.sh` 一键启动独立的验收数据库和网页。该数据库仅监听私有 Unix socket，使用空白虚构测试库；数据保存在 `/private/tmp/cvbuilder-local-acceptance-<UID>`，再次运行会复用。请在自己的终端保持命令运行，验收完按 Ctrl+C 停止。

清空旧 JSON 后，MySQL 从空库开始。旧文件不会被自动读取或导入。每次调整表结构，应增加编号迁移文件并在虚构测试数据库中先验证。
