# Existing data: descriptive statistics and figures

[中文](#中文)

Run from the repository root; no API key, model, GPU or plotting dependency is required:

```bash
uv run simple-ar research-session --config examples/data-analysis/research.toml
```

This directory is one complete case: configuration, input and instructions. The
CSV contains synthetic demonstration values, not a benchmark or experiment.
The configuration explicitly selects `change_percent`, groups by `method` and
states what a row represents. IDs are not selected as metrics. Each group has
three rows: expected means are -2/3 and 3; sample standard deviations are
sqrt(19/3) and 2. No significance or improvement conclusion is requested.

Outputs live under `runs/data-analysis/`, not here. The printed analysis directory
contains `analysis.json`, `analysis.md`, the unchanged input bytes and editable
`figures/*.svg`. Copy that directory and rebuild figures using an installed
SimpleAutoResearch package: `python -m simple_ar.result_analysis.table analysis.json`.
Resume the printed session with `uv run simple-ar research-session --session-root PATH`;
original input updates/deletion do not replace a completed snapshot.

For your data, use `simple-ar start --kind data_analysis` or edit the case config.
Already aggregated means must use `mode = "values"`, with unique row labels;
they are not averaged again and no error bars are guessed. Missing values are
rejected by default; explicitly choose `missing = "omit"` to report omissions.
Do not mix different units across selected columns under one declared unit.
Files default to 20 MiB, outputs to 100 SVG pages; adjust positive `max_mb` and
`max_figures` explicitly if needed. Large categories paginate instead of disappearing.

The analysis is descriptive, not a verification of data collection or semantics.
Review data sensitivity before sharing the delivery directory. No model is used
even if `.env` contains a connection. Complex statistics are not inferred. A later
writing task can use the completed `analysis.json` as `--material`, with its copied
input beside it; values are rechecked and editable figures attached. For supplied
numeric coordinates rather than group means, see [data curves](../data-curves/README.md).

## 中文

仓库根目录执行上面的命令即可。该文件夹只包含一个完整案例；CSV 是明确标注的演示值，
不是研究结果或 benchmark。选择数值列、分组列及每行含义后，生成描述统计和可编辑 SVG，
不调用 API、不训练、不需要绘图库。两组均值应为 -2/3 和 3，样本标准差为 sqrt(19/3) 和 2。
实际产物全部进入 `runs/data-analysis/`；复制完成的分析目录后可用上述模块命令重建图。
续跑使用打印的 session-root，不重新 start。原数据更新或删除不改变已固化输入。

已有汇总表用 `mode = "values"`，标签必须唯一，不再次平均、不猜误差条。
缺失默认拒绝，明确 `omit` 才省略并报告数量；不同单位不要混用同一个单位声明。
物理限制默认 20 MiB/100 页 SVG，可明确调大；分类分页而非丢数据。
描述统计不证明采集、语义、显著性或因果关系；分享前核对敏感数据。不推断复杂统计。
后续写作可将 `analysis.json` 用作 `--material`，保留数据副本，系统复算并附可编辑图。
数值坐标而非分组均值见[曲线案例](../data-curves/README.md)。
