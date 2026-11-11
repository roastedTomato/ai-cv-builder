# 导出检查样例

以下 Word/PDF 文件使用虚构测试资料，不对应真实个人，来自 `fixtures/`。`short`、`long`、`empty` 分别覆盖短内容、长内容跨页和空模块；每种情况均导入三套模板。PDF 均为 A4；检查时未见中文缺字、未替换占位符、文字重叠或截断。

重新生成时请先选择一个新的输出目录，避免覆盖已有文件：

```sh
python scripts/create_render_samples.py --output-dir /tmp/cv-builder-samples
```
