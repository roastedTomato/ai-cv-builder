# 悉尼 MySQL 云端测试环境部署

本说明用于第五阶段开始前的云端 MySQL 准备工作。只使用明确标记的虚构测试账号和数据。保留现有 CloudFront、API Gateway、Cognito、Lambda、临时 S3 桶和 OpenAI 密钥；业务数据改存私有 RDS for MySQL 8.4.9。实际面向用户的第五阶段功能仍以 `IMPLEMENTATION_PLAN.md` 为准。

## 旧环境状态

2026-11-23，AWS 账号 `349744180219` 的 `ap-southeast-2` 区域中，`cvbuilder-sydney-test` 堆栈原有 `RecordsTable` 物理表 `cvbuilder-sydney-test-RecordsTable-O1Y6B9ECT96V` 已按用户要求删除，且没有迁移数据。旧 API 在切换并建表前不可用。旧 CloudFormation 模板仍声明该已删除表；本次模板更新移除该资源，部署前应查看变更集和堆栈漂移。不要用旧模板再次部署。

## 费用与边界

按 730 小时/月、单可用区 `db.t4g.micro`、20 GB gp3、一个 NAT 网关和一个公网 IPv4 估算。2026-11-23 从 AWS 官方 Price List 核对的悉尼费率如下，均未扣除账号额度或抵扣：

| 固定资源 | 费率 | 730 小时/月估算 |
| --- | ---: | ---: |
| RDS MySQL `db.t4g.micro` | US$0.025/小时 | US$18.25 |
| RDS gp3 20 GB | US$0.138/GB 月 | US$2.76 |
| NAT 网关 | US$0.059/小时 | US$43.07 |
| NAT 公网 IPv4 | US$0.005/小时 | US$3.65 |

上述固定部分约 **US$67.73/月**；加上原有 API、Lambda、S3、CloudFront、日志等规划量，`python3 infra/cost_estimate.py` 给出 **US$69.64–76.13/月**。这不是封顶费用，尚未计入 NAT 数据处理、跨可用区传输、额外备份/快照、CPU 突发、税和独立计费的 OpenAI API。[RDS 定价](https://aws.amazon.com/rds/mysql/pricing/)、[NAT 与公网 IPv4 定价](https://aws.amazon.com/vpc/pricing/)可用于重新核对。现有 **US$50/月预算只是提醒，不会停机**，按此配置即使闲置也可能触发告警。部署前在 AWS Pricing Calculator 复核本区域价格，并核对 AWS 当前实际用量和 OpenAI 用量。

数据库仅在两段私有子网内，禁止公网访问。API、后台和建表 Lambda 进入同一 VPC，通过一个 NAT 网关访问 OpenAI、Secrets Manager 和 Lambda API；S3 走网关型 VPC endpoint。[Lambda 接入 VPC 后需要显式外网路径](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc-internet.html)。单 NAT 网关是测试环境的成本折中，所在可用区故障会影响私网出站。数据库为单可用区，不能作为生产高可用设计。

## 构建、部署和建表

需要 AWS CLI、SAM CLI、Docker、Node.js 24+、Python 3.11+ 和目标账号的部署权限。部署前用 `aws sts get-caller-identity` 核对账号；查看 `cvbuilder-sydney-test` 的 CloudFormation 变更集，确保 Cognito、站点桶、临时桶及 OpenAI Secret 不被替换或删除。不要在命令行、仓库或日志中写数据库或 OpenAI 密码。

```sh
sam validate --lint --template-file infra/template.yaml
sam build --template-file infra/template.yaml
sam deploy --guided --stack-name cvbuilder-sydney-test --region ap-southeast-2 --capabilities CAPABILITY_IAM
```

现有堆栈的 `BillingEmail` 和 `CognitoDomainPrefix` 参数应沿用原值。SAM 镜像构建包含 `migrations/001_initial_mysql.sql`；新部署输出 `MySQLMigrationFunction`。RDS 创建完成后，在恢复 API 使用前对该函数**调用一次**，由它在私网中连接数据库并执行建表脚本。示例命令：

```sh
aws lambda invoke --region ap-southeast-2 --function-name <MySQLMigrationFunction 输出值> --cli-binary-format raw-in-base64-out --payload '{}' /tmp/cvbuilder-migration-result.json
```

检查命令输出没有 `FunctionError`，再检查响应文件显示迁移成功，且后续重复调用安全。不要把响应文件提交到仓库。建表失败时先修复数据库连接、密钥或 SQL，不要让页面写入未建表的库。建表后才运行 `python3 infra/publish_site.py --stack cvbuilder-sydney-test --region ap-southeast-2` 发布静态页面。`/static/cloud-config.js` 只包含公开的 API/Cognito 地址和 ID，不包含密钥或数据库地址。

RDS 生成并管理主账号密钥；Lambda 只得到密钥 ARN，运行时从 Secrets Manager 读取用户名和密码。API 与后台 Lambda 继续按 Cognito 用户 ID 隔离记录。库使用自动备份保留 1 天；删除或替换数据库时 CloudFormation 创建快照，快照会产生后续费用。此测试部署使用数据库主账号，正式运营前应改为最小权限的应用账号。

## 验收

1. 建表函数执行成功，重复执行不会产生错误；API 与后台 Lambda 均能通过 VPC 私网访问 RDS，后台仍能调用 OpenAI、访问临时 S3 和读取密钥。
2. 用两个明确标为虚构的账号验证登录、基础简历、公司/职位、版本及面试数据互相隔离。并发创建 V1 和编辑版本时编号不重复；刷新和 Lambda 实例替换后数据仍在。
3. 验证文件上传、提取和导出后台任务的成功与失败路径；临时 S3 对象仍按两天生命周期删除，未完成的分段上传按一天清理。
4. 三份规范化模板的 DOCX/PDF 用虚构中文内容实际渲染，检查字体、换行、分页、重叠、截断和残留占位符。检查 CloudWatch 日志没有简历正文、面试回答或密钥。
5. 查看 RDS、NAT、公网 IPv4 和原服务实际费用，确认 US$50 提醒状态；OpenAI API 费用单独核对。

## 删除测试环境

先确认测试数据、快照和静态文件是否仍需保留；清空静态及临时 S3 桶后删除堆栈。`MySQLDatabase` 的 `DeletionPolicy: Snapshot` 会保留最终快照，`OpenAISecret` 继续 `Retain`；两者需要随后单独核对和清理，以停止相关费用。还应检查 ECR 镜像、日志组、NAT 公网 IPv4、CloudFront、Cognito 和预算等账单项。
