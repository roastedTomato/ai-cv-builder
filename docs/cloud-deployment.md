# 悉尼云端测试环境部署

本说明仅供使用**虚构测试数据**的第四阶段环境。资源位于 `ap-southeast-2`；CloudFront 使用 AWS 默认 HTTPS 地址，API Gateway 使用独立 HTTPS 地址。网页不能直接读写 DynamoDB。部署前确认测试人员所在地在 [OpenAI API 支持地区清单](https://help.openai.com/en/articles/5347006-openai-api-supported-countries-and-territories)内；2026-11-16 查询时，澳大利亚和新西兰在清单中。OpenAI API 消费独立于下述 AWS 预算。

## 部署前费用估算

以下是**规划估算，不是 AWS 报价或消费上限**。假设每月 5 名活跃测试人员、20,000 次 API 调用、200 次后台任务（每次 60 秒、2 GB）、20,000 次静态请求、2 GB CloudFront 出站、DynamoDB 50,000 次读和 20,000 次写、S3 1 GB 临时存储、CloudWatch 日志 1 GB、一个密钥和三条告警。`python3 infra/cost_estimate.py` 对下表规划区间求和，结果为 **US$2.11–8.90/月**。这些区间是预留误差的工作假设，并非已核实的悉尼单价。2026-11-16 已核对下列 AWS 官方定价页面及 OpenAI 支持地区清单；当前账号额度和悉尼区实际报价仍需在部署前，将相同用量输入 [AWS Pricing Calculator](https://calculator.aws/) 核对。估算未包括税、备份增长、超出假设的流量或 OpenAI API。

| 服务 | 月度规划区间（USD） | 主要变量与价格来源 |
| --- | ---: | --- |
| Lambda API 与后台任务 | 0.80–3.00 | 调用量、GB 秒、镜像冷启动；[Lambda 定价](https://aws.amazon.com/lambda/pricing/) |
| API Gateway HTTP API | 0.10–0.50 | 请求次数、出站流量；[API Gateway 定价](https://aws.amazon.com/api-gateway/pricing/) |
| DynamoDB 按需与恢复点 | 0.20–1.00 | 读写单位、数据体积、PITR；[DynamoDB 定价](https://aws.amazon.com/dynamodb/pricing/) |
| S3 临时与静态桶 | 0.10–0.50 | GB 月、请求次数；[S3 定价](https://aws.amazon.com/s3/pricing/) |
| CloudFront | 0.20–2.00 | 出站量、HTTPS 请求、可用额度；[CloudFront 定价](https://aws.amazon.com/cloudfront/pricing/) |
| Cognito Lite | 0.00 | 5 名用户处于当前 10,000 MAU 免费层内，需核对账号共享额度；[Cognito 定价](https://aws.amazon.com/cognito/pricing/) |
| CloudWatch 日志与告警 | 0.20–1.00 | 日志摄入、保留和告警；[CloudWatch 定价](https://aws.amazon.com/cloudwatch/pricing/) |
| Secrets Manager | 0.41–0.50 | 一个密钥及读取；[Secrets Manager 定价](https://aws.amazon.com/secrets-manager/pricing/) |
| ECR 镜像 | 0.10–0.30 | 镜像大小；[ECR 定价](https://aws.amazon.com/ecr/pricing/) |
| SNS 与 AWS Budgets | 0.00–0.10 | 告警邮件；[Budgets 定价](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/) |

CloudFormation 创建一个 **US$50/月** AWS 成本预算，在实际成本达到 80% 或预测达到 100% 时向账单邮箱发信；它**不会自动停机**。SNS 告警订阅邮件需人工确认。OpenAI 需在其平台单独检查项目余额、使用量和限额；不要将 OpenAI 密钥写入参数、仓库或命令行日志。当前 AWS 账号/额度无法从本地环境代查时，应在部署前用 `aws sts get-caller-identity`、Billing/Cost Explorer、Free Tier 页面和 OpenAI 控制台人工确认。

## 创建与发布

需要 AWS CLI、SAM CLI、Docker、Python 3.11+、对目标账号的部署权限，以及已启用的 AWS 账单访问。镜像和 Lambda 都指定单一 `arm64` 架构；x86 开发机需要能执行 Docker 跨架构构建。以下命令以仓库根目录为工作目录；`--guided` 首次运行时选择 `ap-southeast-2`、提供唯一 Cognito 前缀和接收账单提醒的邮箱，并允许创建 IAM 角色。CloudFront 可在部署后数分钟才完成分发。发布脚本只上传网页静态文件，并在临时目录生成公开的站点配置，不提交账号配置。

```bash
sam build --template-file infra/template.yaml
sam deploy --guided --region ap-southeast-2 --capabilities CAPABILITY_IAM
python3 infra/publish_site.py --stack <stack-name> --region ap-southeast-2
```

从堆栈输出读取 `SecretArn`，在 AWS Secrets Manager 控制台把占位随机值替换为实际 OpenAI API 密钥。该密钥仅授予后台 Lambda 读取；不要将值放进 shell 历史或镜像。可使用 [Secrets Manager 控制台](https://docs.aws.amazon.com/secretsmanager/latest/userguide/manage_update-secret-value.html)完成编辑。`publish_site.py` 输出网页 HTTPS 地址；`ApiUrl` 是独立 API 地址，`HostedUiDomain` 是 Cognito 登录地址。网页通过部署生成的 `/static/cloud-config.js` 加载这些公开 ID 和 URL。

后续更新重复执行 `sam build`、`sam deploy` 和 `publish_site.py`。先运行 `sam validate --template-file infra/template.yaml --lint`，查看变更集，尤其确认 DynamoDB 表和密钥不会被替换。容器镜像仅复制应用代码、`web/`、三份规范模板和 Schema，不复制本机 JSON、测试夹具或样例导出。`Dockerfile.cloud` 安装 LibreOffice 与 Noto CJK；云端实际 PDF 仍需按验收步骤确认字体替换及分页。

## 验收与运行

1. 用两组明确标为虚构的测试账号，在 CloudFront 页面完成注册、邮件确认、登录和退出；未带 Bearer access token 调用 `/api/resume` 应得到 401。
2. 分别录入/导入并确认基础简历，建立多公司与多岗位。每个岗位只调用 AI 生成 V1；编辑 V1 后保存为 V2，再编辑 V2 保存为 V3，确认旧版保留且不能再次调用 AI。测试公司和岗位改名、删除公司及岗位时的级联清理、单独删除任意版本；删除版本后编号不重排、不复用，删光版本也不能再次调用 AI。跨账号读取、编辑、删除、重置、导出都不能影响另一账号；并发保存不得重复编号。
3. 重新调用 API（模拟 Lambda 实例替换），确认版本、模块顺序、外观和任务状态仍在。用接近 15 MB 的**虚构测试文件**验证上传限制；让后台任务失败一次，确认页面显示可理解的失败信息。
4. 对三份模板分别下载 DOCX/PDF，实际打开或渲染检查中文字体、换行、分页、重叠、截断和残留 `{{...}}`。记录 PDF 转换时间；仅通过自动化单元测试不能代替此项。
5. 查 CloudWatch 两个 Lambda 日志组和失败告警，确认日志没有简历正文、上传内容或密钥。S3 生命周期为整个临时桶 2 天，覆盖 `uploads/`、`exports/`、`results/`；按 S3 生命周期的异步执行特性复查对象是否到期清理。

CloudWatch 日志保留 14 天。DynamoDB 开启 PITR，表和密钥设置 `Retain` 以避免删除堆栈时误删测试数据。需要恢复时，在 DynamoDB 控制台选择时间点恢复到**新表**，先验证内容，再更新部署配置指向恢复表；恢复会产生额外费用。运行成本按月在 AWS Cost Explorer 按服务核对，并留意 ECR 镜像、PITR、CloudFront 出站和日志增长。

单条基础简历、岗位或版本写入在 350 KiB 时由应用拒绝，以留出 DynamoDB 400 KiB 单项限制的余量。15 MB 是上传文件大小上限，并非可保存的结构化简历大小。重置与同一账号的新写入同时发生时不保证原子清空，验收时应先停止该账号的新任务再重置。

## 删除测试环境

先确认导出与备份需求。清空静态桶和临时桶，随后执行 `sam delete --stack-name <stack-name> --region ap-southeast-2`。堆栈删除会保留 DynamoDB 表和 OpenAI 密钥；确认无需恢复后，再分别在 DynamoDB 与 Secrets Manager 控制台删除。ECR 中由 SAM 创建的镜像仓库及 CDK/SAM bootstrap 资源可能需要另行核对。删除后检查 CloudFormation、S3、Lambda、API Gateway、CloudFront、Cognito、CloudWatch、SNS、Budgets 和 ECR 账单项，避免继续计费。
