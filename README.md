# AI 简历生成平台：第一阶段验证 CLI

本仓库实现第一阶段的 Python 命令行验证工具：招聘文本结构化、基础简历匹配、岗位简历生成与事实来源校验，以及规范化 Word 模板填充和 PDF 导出。它不包含完整网站、账号、数据库或版本管理。

## 环境

- Python 3.11 或更高版本
- LibreOffice（提供 `soffice`），用于 PDF 导出。程序会查找 PATH、常见 macOS 安装位置和 Codex 本机渲染器；也可用 `SOFFICE_PATH` 指定可执行文件
- OpenAI API key；ChatGPT 订阅本身不等同于 API 额度

安装依赖：

```sh
uv sync
```

配置 API：

```sh
export OPENAI_API_KEY="你的 API key"
export OPENAI_MODEL="gpt-5.5"  # 可按 API 账户可用模型覆盖
export SOFFICE_PATH="/Applications/LibreOffice.app/Contents/MacOS/soffice"  # 仅在自动发现失败时配置
```

API key 只从环境变量读取，不应写入代码、配置文件或提交记录。API 的计费与 ChatGPT 订阅分开。

## 常用命令

```sh
cvbuilder validate base fixtures/base-resume.sample.json
cvbuilder validate job fixtures/job-requirement.sample.json
cvbuilder validate generated fixtures/generated-resume.sample.json
cvbuilder match fixtures/base-resume.sample.json fixtures/job-requirement.sample.json
cvbuilder parse-job fixtures/job-requirement.sample.txt --id test-job-001 --output /tmp/job.json
cvbuilder generate fixtures/base-resume.sample.json /tmp/job.json --output /tmp/generated.json
cvbuilder make-templates
cvbuilder export /tmp/generated.json --template templates/template-01.docx --output /tmp/resume.docx
```

`cvbuilder demo` 会用虚构测试资料调用 API 完成招聘文本解析、匹配、生成和三套模板导出：

```sh
cvbuilder demo --output-dir sample_exports
```

每次模型请求使用 Responses API Structured Outputs，并设置 `store=false`；客户端重试次数限制为 1。生成结果还会在本地通过 JSON Schema、来源 ID、技能/证书原文、个人信息、机构、日期和数字断言检查。数字检查无法证明所有自然语言改写在语义上完全等同原文；用户确认仍是事实准确性的最终依据。

## 模板

`cv_templates/` 中的两份源模板保留原样。`templates/` 中的三份实验模板使用 [统一占位符规范](docs/word-template-spec.md)，由本项目生成。支持短内容、重复模块、空模块和自然分页；不承诺兼容任意 DOCX。

## 测试与人工检查

```sh
python -m unittest discover -s tests -v
python scripts/create_render_samples.py
```

渲染样例写入 `sample_exports/`，包含短、长和空模块分别填入三个模板的 DOCX/PDF。人工验收需打开生成的 PDF 或转成页面图，检查中文字体、换行、分页、重叠、截断和意外空白页。

所有 fixture 均为虚构测试数据，见 [fixtures/README.md](fixtures/README.md)。
