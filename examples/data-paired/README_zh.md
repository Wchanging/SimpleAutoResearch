# 配对观察（无需 API）

这是人工构造的匹配耗时，不是实验或 benchmark 成绩。在仓库根运行：

```bash
uv run simple-ar research-session --config examples/data-paired/research.toml \
  --interaction autonomous
```

`runs/data-paired/` 保存输入副本、`analysis.json`、可读报告、可编辑 SVG、矢量 PDF 和 PNG 预览。
差值为候选减基线：A 三对，平均 −5/3 秒、标准误 1/3；B 两对，平均 2 秒、标准误 1。
B 有两行联合省略，不能对分别过滤后的均值相减。差值图显示 ±1 标准误，
假定各对独立，不是置信区间或显著性检验；耗时较少本身也不证明方法有效。
在 `[analysis]` 设置 `plot = "box"` 可展示各方法分布，同时保留独立配对差值图；
默认 `bar` 只展示均值。

完成的 `analysis.json` 可交给 `start --kind writing --material PATH`，旁边需保留数据副本。
写作是另一个需要模型的任务。继续已有运行用打印的 `--session-root`，不重新启动案例。

[English](README.md) · [配置说明](../../docs/CONFIG_REFERENCE_zh.md)
