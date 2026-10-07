"""Scientific data figures from computed values, using one headless renderer."""
import math
from io import StringIO
from pathlib import Path
import textwrap


def _label(value: str, width: int = 30) -> str:
    lines = textwrap.wrap(str(value), width=width) or [""]
    if len(lines) > 3:
        lines = [*lines[:2], textwrap.shorten(" ".join(lines[2:]), width=width, placeholder="…")]
    return "\n".join(lines)


def _figure(spec: dict, height: float):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    fig = Figure(figsize=(7 if spec["width"] == "wide" else 3.5, height), layout="constrained")
    FigureCanvasAgg(fig)
    return fig, fig.subplots()


def _scale(values: list) -> float:
    magnitude = max(abs(value) for value in values)
    # Keep ordinary values in their units so the library chooses useful ticks.
    return magnitude if magnitude > 1e150 or 0 < magnitude < 1e-150 else 1


def _numeric_axis(ax, which: str, values: list, *, zero: bool = False, errors: list | None = None, pad: bool = False) -> float:
    """Scale before subtracting or adding errors; preserve finite extreme data."""
    from matplotlib.ticker import FuncFormatter
    scale = _scale([*values, *(errors or [])])
    normalized = [value / scale for value in values]
    endpoints = [v + sign * error / scale for v, error in zip(normalized, errors or [])
                 for sign in (-1, 1)]
    bounds = [*normalized, *endpoints, *([0] if zero else [])]
    low, high = min(bounds), max(bounds)
    if low == high:
        if low == 0:
            scale, low, high = 1, -1, 1
        else:
            low, high = low - .1, high + .1
            if scale != 1:
                low, high = max(-1, low), min(1, high)
    if pad:
        margin = (high - low) * .04
        low, high = low - margin, high + margin
    getattr(ax, f"set_{which}lim")(low, high)
    getattr(ax, f"{which}axis").set_major_formatter(
        FuncFormatter(lambda value, _: f"{value * scale:.3g}" if math.isfinite(value * scale) else ""))
    return scale


def _save(fig, spec: dict, output_dir: Path, filename: str, caption: str, encoding: dict) -> dict:
    """One solved layout, three formats, one logical figure."""
    path = output_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    fig.set_layout_engine("none")
    for suffix in ("svg", "pdf", "png"):
        metadata = {"Date": None} if suffix == "svg" else {"CreationDate": None} if suffix == "pdf" else None
        if suffix == "svg":
            stream = StringIO()
            fig.savefig(stream, format="svg", dpi=300, metadata=metadata)
            svg = stream.getvalue()
            # Omit the library's unused external DTD in our generated SVG.
            # Untrusted imports still pass the exporter's resource checks.
            path.write_text(svg[svg.index("<svg "):], encoding="utf-8")
        else:
            fig.savefig(path.with_suffix("." + suffix), dpi=300, metadata=metadata)
    return {"path": filename, "exports": {suffix: str(Path(filename).with_suffix("." + suffix)).replace("\\", "/")
                                          for suffix in ("pdf", "png")},
            "caption": caption, "width": spec["width"], "visual_check": "not_performed", "encoding": encoding}


def render_table_figures(result: dict, output_dir: Path) -> list[dict]:
    """Keep statistical semantics in table; render without a GUI or global backend."""
    from matplotlib import rc_context
    with rc_context({"svg.fonttype": "none", "svg.hashsalt": "simple-ar-data",
                     "font.size": 9, "text.parse_math": False, "text.usetex": False}):
        plot = result["spec"].get("plot", "bar")
        if plot == "heatmap":
            return _heatmap_figures(result, output_dir)
        if plot in {"line", "scatter"}:
            return _coordinate_figures(result, output_dir)
        return _value_figures(result, output_dir)


def render_measurement_pairs(pairs: list[dict], *, title: str, unit: str,
                             output_dir: Path, filename: str) -> dict:
    """Render recorded pairs without averaging or inferring uncertainty."""
    from matplotlib import rc_context

    spec = {"width": "wide"}
    caption = "Per-seed measured pairs under their declared conditions; descriptive only, without an aggregate or significance claim."
    with rc_context({"svg.fonttype": "none", "svg.hashsalt": "simple-ar-data",
                     "font.size": 9, "text.parse_math": False, "text.usetex": False}):
        fig, ax = _figure(spec, max(2.5, .38 * len(pairs) + 1.6))
        values = [row[role] for row in pairs for role in ("baseline", "candidate")]
        scale = _numeric_axis(ax, "x", values, pad=True)
        positions = list(range(len(pairs)))
        ax.hlines(positions, [row["baseline"] / scale for row in pairs],
                  [row["candidate"] / scale for row in pairs], color="#95a1ad", linewidth=1)
        for role, color in (("baseline", "#687583"), ("candidate", "#1268b3")):
            ax.scatter([row[role] / scale for row in pairs], positions, label=role, color=color)
        ax.set(yticks=positions, yticklabels=[_label(f"Seed {row['seed']}") for row in pairs],
               ylim=(len(pairs) - .5, -.5), xlabel=_label(unit or "unit not recorded"))
        ax.set_title(_label(title, 55))
        ax.legend()
        return _save(fig, spec, output_dir, filename, caption,
                     {"plot": "paired_points", "pairs": pairs, "uncertainty": "not_shown"})


def _value_figures(result: dict, output_dir: Path) -> list[dict]:
    spec = result["spec"]
    box_requested = spec.get("plot") == "box"
    records = result["records"]
    if box_requested:
        summaries = {(r["group"], r["column"]): r["value"] for r in result.get("observation_summaries", [])}
        if any((r["group"], r["column"]) not in summaries for r in records):
            raise ValueError("Box plots need computed observation distributions; reload the analysis package.")
        records = [dict(r, **summaries[r["group"], r["column"]]) for r in records]
    panels = [(col, [r for r in records if r["column"] == col], False) for col in spec["value_columns"]]
    paired = {}
    for row in result.get("paired_comparisons", []):
        if row["count"]:
            name = f"{row['candidate_column']} − {row['baseline_column']}"
            paired.setdefault(name, []).append({**row, "mean": row["mean_difference"], "missing": row["missing_pairs"]})
    panels.extend((name, rows, True) for name, rows in paired.items())
    planned = sum((len(rows) + 11) // 12 for _, rows, _ in panels)
    if planned > spec.get("max_figures", 100):
        raise ValueError(f"Data needs {planned} figure pages; exceeds physical max_figures; no data was silently dropped.")
    shared_values = ([v for r in records for v in ((r["min"], r["max"]) if box_requested else
                     (r["mean"] if spec["mode"] == "observations" else r["value"],))]
                     if spec.get("paired_baseline") else None)
    figures = []
    for metric_index, (column, selected, difference) in enumerate(panels, 1):
        box = box_requested and not difference
        values = [r["mean"] if spec["mode"] == "observations" else r["value"] for r in selected]
        errors = [r.get("standard_error") or 0 for r in selected] if difference else None
        axis_values = ([v for r in selected for v in (r["min"], r["max"])] if box else values)
        if not difference and shared_values is not None:
            axis_values = shared_values
        unit = spec.get("value_unit") or "unit not supplied"
        for offset in range(0, len(selected), 12):
            page = selected[offset:offset + 12]
            fig, ax = _figure(spec, max(2.5, .55 * len(page) + 1.6))
            scale = _numeric_axis(ax, "x", axis_values, zero=not box, errors=errors, pad=box or difference)
            labels = [_label(f"{r['group']} (n={r['count']}; missing={r['missing']})"
                             if difference or box else r["group"], 30 if spec["width"] == "wide" else 18) for r in page]
            positions = list(range(len(page)))
            if box:
                stats = [dict(whislo=r["min"] / scale, q1=r["q1"] / scale, med=r["median"] / scale,
                              q3=r["q3"] / scale, whishi=r["max"] / scale, fliers=[]) for r in page]
                ax.bxp(stats, positions=positions, orientation="horizontal", showfliers=False, manage_ticks=False)
                subtitle = "Box = Q1–Q3; line = median\nWhiskers = min–max; not uncertainty"
                caption = (f"{column}: Q1–Q3 boxes with median and min–max whiskers ({unit}), "
                           "using all nonmissing observations per group. Whiskers are not confidence intervals; "
                           "no observations are classified as outliers.")
                shown = ["min", "q1", "median", "q3", "max"]
            else:
                values_page = [r["mean"] if spec["mode"] == "observations" else r["value"] for r in page]
                bars = ax.barh(positions, [v / scale for v in values_page], color="#286a9b")
                if difference:
                    uncertain = [(i, r) for i, r in enumerate(page) if r.get("standard_error") is not None]
                    if uncertain:
                        ax.errorbar([r["mean"] / scale for _, r in uncertain], [i for i, _ in uncertain],
                                    xerr=[r["standard_error"] / scale for _, r in uncertain], fmt="none", color="#203040", capsize=3)
                    subtitle = "Paired mean; whiskers = ±1 SE (n ≥ 2)"
                    caption = (f"{column}: matched-row mean difference ({unit}); whiskers show ±1 standard error "
                               "when at least two pairs exist, assuming independent pairs, not a confidence interval. "
                               "Unmatched rows are excluded. Positive does not imply improvement.")
                    shown = ["mean_difference"] + (["standard_error"] if uncertain else [])
                else:
                    subtitle = ""
                    caption = (f"{column}: {'group means' if spec['mode'] == 'observations' else 'supplied values'}; "
                               f"value unit (user-declared): {unit}; observations: {spec['observation_unit']}. No uncertainty bars.")
                    shown = ["mean"] if spec["mode"] == "observations" else ["value"]
                bars.set_label("computed values")
                ax.axvline(0, color="#687583", linewidth=.7)
            if not difference and shared_values is not None:
                caption += " Method panels share the same numeric scale."
            ax.set(yticks=positions, yticklabels=labels, ylim=(len(page) - .5, -.5), xlabel=_label(unit))
            ax.set_title(_label(column, 55 if spec["width"] == "wide" else 28) + ("\n" + subtitle if subtitle else ""))
            figures.append(_save(fig, spec, output_dir, f"figures/value-{metric_index}-{offset // 12 + 1}.svg", caption,
                {"plot": "box" if box else "bar", "statistics_shown": shown,
                 "role": "matched_difference" if difference else "marginal",
                 "value_columns": [page[0]["baseline_column"], page[0]["candidate_column"]] if difference else [column],
                 "groups": [r["group"] for r in page], "row_level_values_shown": spec["mode"] == "values"}))
    return figures


def _heatmap_figures(result: dict, output_dir: Path) -> list[dict]:
    from matplotlib import colormaps
    from matplotlib.ticker import FuncFormatter
    spec = result["spec"]
    groups = list(dict.fromkeys(r["group"] for r in result["records"]))
    cells = {(r["group"], r["column"]): r["value"] for r in result["records"]}
    present = [v for v in cells.values() if v is not None]
    if not present:
        raise ValueError("A heatmap needs at least one observed numeric value; all cells are missing.")
    low, high = min(present), max(present)
    scale = _scale(present)
    columns_per_page = 6 if spec["width"] == "wide" else 3
    pages = [(groups[i:i + 12], spec["value_columns"][j:j + columns_per_page])
             for i in range(0, len(groups), 12) for j in range(0, len(spec["value_columns"]), columns_per_page)]
    if len(pages) > spec["max_figures"]:
        raise ValueError("Matrix exceeds physical max_figures; no rows or columns were silently dropped.")
    if any(len(rows) * len(cols) > spec["max_points"] for rows, cols in pages):
        raise ValueError("Matrix page exceeds physical max_points (cells); no cells were sampled.")
    unit = spec.get("value_unit") or "unit not supplied"
    figures = []
    for index, (rows, cols) in enumerate(pages, 1):
        fig, ax = _figure(spec, max(2.5, .4 * len(rows) + 1.4))
        values = [[cells[g, c] / scale if cells[g, c] is not None else math.nan for c in cols] for g in rows]
        mesh = ax.pcolormesh(values, cmap=colormaps["Blues"].with_extremes(bad="#b5b5b5"),
                            vmin=low / scale, vmax=high / scale, edgecolors="white")
        ax.set(xticks=[i + .5 for i in range(len(cols))], xticklabels=[_label(c, 12) for c in cols],
               yticks=[i + .5 for i in range(len(rows))], yticklabels=[_label(g, 24) for g in rows])
        ax.invert_yaxis()
        ax.set_title("Raw-value heatmap\nShared scale; gray / NA = missing")
        # A constant matrix has no color range; do not let colorbar invent one.
        if low != high:
            bar = fig.colorbar(mesh, ax=ax, format=FuncFormatter(lambda v, _: f"{v * scale:.3g}"))
            bar.solids.set_rasterized(False)
            bar.set_label(_label(unit))
        else:
            ax.set_xlabel(_label(f"All observed values: {low:.3g} ({unit})"))
        for i, group in enumerate(rows):
            for j, col in enumerate(cols):
                value = cells[group, col]
                foreground = "white" if value is not None and low != high and (value / scale - low / scale) / (high / scale - low / scale) > .6 else "#203040"
                ax.text(j + .5, i + .5, f"{value:.3g}" if value is not None else "NA",
                        ha="center", va="center", color=foreground, fontsize=8)
        figures.append(_save(fig, spec, output_dir, f"figures/matrix-{index}.svg",
            f"Supplied matrix values ({unit}); full-matrix color scale [{low:.12g}, {high:.12g}] on all pages. Gray/NA cells are missing, not zero. No normalization or clustering.",
            {"plot": "heatmap", "statistics_shown": ["value"], "value_columns": list(cols),
             "groups": rows, "row_level_values_shown": True}))
    return figures


def _coordinate_figures(result: dict, output_dir: Path) -> list[dict]:
    from matplotlib.lines import Line2D
    spec = result["spec"]
    columns = spec["value_columns"]
    shared = spec.get("series_layout", "separate") == "shared"
    panels = [columns] if shared else [[c] for c in columns]
    by_column = {c: [r for r in result["records"] if r["column"] == c] for c in columns}
    pages = []
    for panel in panels:
        groups = list(dict.fromkeys(r.get("series_group", "") for c in panel for r in by_column[c]))
        per_page = max(1, 6 // len(panel)) if spec.get("group_column") else len(groups)
        pages.extend((panel, groups[i:i + per_page], groups) for i in range(0, len(groups), per_page))
    if len(pages) > spec["max_figures"]:
        raise ValueError("Selected coordinate columns/groups exceed physical max_figures; no figures were silently dropped.")
    if any(sum(r["column"] in panel and r.get("series_group", "") in groups for r in result["records"]) > spec["max_points"]
           for panel, groups, _ in pages):
        raise ValueError("Figure exceeds physical max_points; no points were sampled.")
    figures = []
    colors = ("#286a9b", "#ba5818", "#25734a", "#8b469c", "#a04159", "#626565")
    for index, (panel, groups, all_groups) in enumerate(pages, 1):
        full = [r for r in result["records"] if r["column"] in panel]
        points = [r for r in full if r.get("series_group", "") in groups]
        present = [r for r in points if r["value"] is not None and r["x"] is not None]
        axis_present = [r for r in full if r["value"] is not None and r["x"] is not None]
        fig, ax = _figure(spec, 3.6 if spec["width"] == "wide" else 3)
        sx = _numeric_axis(ax, "x", [r["x"] for r in full if r["x"] is not None], pad=True)
        sy = _numeric_axis(ax, "y", [r["value"] for r in axis_present], pad=True)
        radius = (min(1.8, max(.45, math.sqrt(.15 * (400 if spec["width"] == "wide" else 150) * 160
                  / (math.pi * len(axis_present))))) if spec["plot"] == "scatter" else 1.8)
        opacity = .5 if radius < 1.8 else 1
        styles, handles = {}, []
        for c in panel:
            for group in groups:
                style = panel.index(c) * len(all_groups) + all_groups.index(group)
                color = colors[style % len(colors)]
                dash = ("-", "--", ":")[style % 3]
                label = f"{c} — {group}" if spec.get("group_column") else c
                styles[c, group] = color
                if spec["plot"] == "line":
                    selected = sorted((r for r in by_column[c] if r.get("series_group", "") == group), key=lambda r: r["x"])
                    ax.plot([r["x"] / sx for r in selected],
                            [r["value"] / sy if r["value"] is not None else math.nan for r in selected], color=color, linestyle=dash)
                handles.append(Line2D([], [], color=color, linestyle=dash if spec["plot"] == "line" else "none",
                                      marker="o", markersize=2 * radius, label=_label(label, 35)))
        points_artist = ax.scatter([r["x"] / sx for r in present], [r["value"] / sy for r in present],
                                  c=[styles[r["column"], r.get("series_group", "")] for r in present],
                                  s=(2 * radius) ** 2, alpha=opacity)
        points_artist.set_gid("data-points")
        if shared or spec.get("group_column"):
            ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(.5, 1.25), ncols=2, frameon=False)
        column = " / ".join(panel)
        ax.set(xlabel=_label(f'{spec["x_column"]} ({spec["x_unit"] or "unit not supplied"})'),
               ylabel=_label(f'{"Values" if shared else column} ({spec["value_unit"] or "unit not supplied"})'))
        caption = (f'{column} versus {spec["x_column"]}: {spec["plot"]} of supplied coordinates; '
                   f'x unit: {spec["x_unit"] or "not supplied"}; value unit: {spec["value_unit"] or "not supplied"}. '
                   f'{len(present)} plotted, {len(points) - len(present)} missing {"coordinates" if any(r["x"] is None for r in points) else "y values"}. '
                   + ("Ordered by numeric x; missing y values break the line. " if spec["plot"] == "line" else "Duplicate x coordinates are retained. ")
                   + ("Series share axes." if shared else "Each value column has its own axis."))
        if spec.get("group_column"):
            caption += f' Series grouped by {spec["group_column"]}: {", ".join(groups)}. Group pages use the same axes.'
        if opacity != 1:
            caption += " Smaller translucent markers reduce overplotting; every complete coordinate pair is retained."
        figures.append(_save(fig, spec, output_dir, f'figures/{spec["plot"]}-{index}.svg', caption,
            {"plot": spec["plot"], "statistics_shown": ["value"], "value_columns": list(panel),
             "x_column": spec["x_column"], "groups": groups, "row_level_values_shown": True,
             "point_radius": radius, "point_opacity": opacity, "point_order": "supplied_record_order"}))
    return figures
