# Supplied numerical curves / 已有数值曲线

One complete case, run from the repository root without an API or GPU. The plotting
library is installed with the package and needs no GUI or separate setup:

```bash
uv run simple-ar research-session --config examples/data-curves/research.toml
```

These are five synthetic demonstration checkpoints, not an actual training run or
benchmark. The two loss columns are plotted separately, in the explicitly declared
unit. `loss_b` is missing at step 2: its record remains in `analysis.json`, no zero
is substituted and the line breaks there. Each SVG has its own axis; neither curve
is smoothed or fitted. Lines order by numeric x and refuse duplicate x rather than
average replicates. For real coordinate pairs with repeated x, choose `plot = "scatter"`.
Value columns have separate axes by default; an explicit grouping column can
separate category series. Statistical estimates and error bars are not guessed.

Outputs go to `runs/data-curves/`. Copy the printed analysis directory to preserve
`analysis.json`, `analysis.md`, input bytes, editable SVGs, vector PDFs and PNG previews. Rebuild inside that
directory using the installed package:

```bash
python -m simple_ar.result_analysis.table analysis.json
```

Resume with `simple-ar research-session --session-root PATH`; completed input is
not replaced if the original changes. For later writing, use the completed
`analysis.json` as `--material` and keep its copied input beside it. Source collection,
column semantics and scientific validity are not certified. Review privacy before sharing.
Limits are adjustable: `max_mb` for input bytes, `max_figures` for output count,
`max_points` (default 10000) for rows per coordinate figure. Excess fails without sampling.

## 中文

这个文件夹只是一份完整的曲线案例；上面的命令无需 API/GPU，绘图库随包安装，无需 GUI。
CSV 是五个演示坐标，不是实际训练或 benchmark。两列 loss 各自分轴绘图，
`loss_b` 在 step=2 的缺失保留在记录中，折线断开，不补零、不平滑、不拟合。
折线按数值 x 排序，重复 x 会拒绝，避免无依据平均；重复坐标对可选 `plot = "scatter"`。
本例两列分轴；自己的数据可用 group_column 明确多组系列，不自动统计或猜误差条。
图形同时提供可编辑 SVG、矢量 PDF 和 PNG 预览。

产物在 `runs/data-curves/`；复制完整分析目录后可按上述命令重建图，按打印的 session-root
恢复，不会重读已改变的原始输入。后续写作用 `analysis.json` 作为 `--material`，保留数据副本。
分享前核查隐私；数值交付不认证数据采集、列语义或科学结论。物理上限可显式调整，不悄悄抽样。
