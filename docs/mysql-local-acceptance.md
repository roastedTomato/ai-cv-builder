# MySQL 前置工作：本地验收

验收日期：2026-11-23。使用仅绑定本机 Unix socket、关闭网络监听的隔离 MySQL 8.4.9 实例；所有写入均为明确标记的虚构测试数据，并在检查后清理。

- `migrations/001_initial_mysql.sql` 在空库和已有表的测试库上各执行一次，8 张表存在，重复执行无错误。
- `CV_MYSQL_TEST_DATABASE=cvbuilder_schema_test CV_MYSQL_USER=root CV_MYSQL_SOCKET=... .venv/bin/python -m unittest discover -s tests -q`：148 项通过，包括 MySQL 用户隔离、并发 V1、版本编号、修订号、面试快照、状态冲突和真实本地 API 流程。
- 本地 Uvicorn 服务在 `127.0.0.1:8765` 启动；实际 HTTP 请求完成空库读取、虚构基础简历保存、公司与职位创建、要求修改、列表读取和重置，响应均符合预期。
- 通过本地 HTTP 导出虚构数据的 DOCX 和 PDF。DOCX 无残留占位符；PDF 为一页 A4，文本可提取。实际渲染为 PNG 后人工查看，中文字体、换行、分页均正常，未见重叠或截断。
- 保存虚构记录后停止并重启隔离 MySQL 服务，重新连接可读出记录；随后已清理该记录。

此验收覆盖 MySQL 前置工作的本地存储切换，不代表第五阶段全部功能或未来云端部署验收完成。
