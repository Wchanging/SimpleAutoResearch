"""Editable scientific data figures; no prose mining or inferred statistics."""
from pathlib import Path

from simple_ar.core.artifacts import write_text


def render_table_figures(result: dict, output_dir: Path) -> list[dict]:
    """Render explicit computed table values, not numbers mined from prose.

    Separate metrics have separate axes. Pagination preserves all categories;
    no aggregate, error bar or comparison verdict is inferred by the renderer.
    SVG is both output and editable source; the result/data retain full labels.
    """
    import textwrap
    from html import escape

    spec = result["spec"]
    if spec.get("plot", "bar") != "bar":
        return _coordinate_figures(result, output_dir)
    wide = spec["width"] == "wide"
    width, left = (720, 245) if wide else (360, 145)
    right = width - 40
    records = result["records"]
    by_column = {name: [] for name in spec["value_columns"]}
    for row in records:
        by_column[row["column"]].append(row)
    planned = sum((len(rows) + 11) // 12 for rows in by_column.values())
    if planned > spec.get("max_figures", 100):
        raise ValueError(f"Data needs {planned} figure pages; exceeds physical max_figures={spec.get('max_figures', 100)}. Select fewer columns/groups or explicitly increase the output limit; no data was silently dropped.")
    figures = []
    for metric_index, column in enumerate(spec["value_columns"], start=1):
        selected = by_column[column]
        for offset in range(0, len(selected), 12):
            page = selected[offset:offset + 12]
            values = [row["mean"] if spec["mode"] == "observations" else row["value"] for row in page]
            low, high = min(0, *values), max(0, *values)
            # Normalize before subtracting: large finite values of opposite
            # signs must not overflow the axis range.
            scale = max(abs(low), abs(high), 1e-300)
            lo, hi = low / scale, high / scale
            if lo == hi:
                lo, hi = -1, 1
                scale = 1
            def x(value):
                return left + (value / scale - lo) / (hi - lo) * (right - left)
            y, labels = 40, []
            for row in page:
                parts = textwrap.wrap(row["group"], width=29 if wide else 16) or [row["group"]]
                shown = parts[:3]
                if len(parts) > 3:
                    shown[-1] = shown[-1][:-1] + "…"
                height = max(40, len(shown) * 17 + 12)
                labels.append((row, shown, y, height))
                y += height
            unit = spec.get("value_unit") or "unit not supplied"
            unit_lines = textwrap.wrap(unit, width=max(8, int((right - left) / 8.4))) or [unit]
            displayed_unit = unit_lines[:2]
            if len(unit_lines) > 2:
                displayed_unit[-1] = displayed_unit[-1][:-1] + "…"
            height = y + 36 + 17 * len(displayed_unit)
            physical_width = "7in" if wide else "3.5in"
            svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{physical_width}" height="{height / width * (7 if wide else 3.5):.4f}in" viewBox="0 0 {width} {height}">',
                   f'<title>{escape(column)}; descriptive {escape(spec["mode"])}</title>',
                   '<rect width="100%" height="100%" fill="white"/>',
                   '<g font-family="sans-serif" font-size="14" fill="#203040">',
                   f'<text x="8" y="22" font-weight="bold">{escape(textwrap.shorten(column, width=70 if wide else 34, placeholder="…"))}</text>',
                   f'<line x1="{x(0):.4f}" y1="20" x2="{x(0):.4f}" y2="{y}" stroke="#687583"/>']
            for row, shown, top, bar_height in labels:
                value = row["mean"] if spec["mode"] == "observations" else row["value"]
                svg.append(f'<g><title>{escape(row["group"])}: {value:.12g}; n={row["count"]}; missing={row["missing"]}</title>')
                for line_index, text in enumerate(shown):
                    svg.append(f'<text x="8" y="{top + 16 + line_index * 17}">{escape(text)}</text>')
                svg.append(f'<rect x="{min(x(0), x(value)):.4f}" y="{top + 6}" width="{abs(x(value) - x(0)):.4f}" height="18" fill="#286a9b"/></g>')
            svg.append(f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" stroke="#687583"/>')
            for i in range(3):
                value = (lo + (hi - lo) * i / 2) * scale
                svg.append(f'<text x="{x(value):.4f}" y="{y + 20}" text-anchor="middle">{value:.3g}</text>')
            for i, line in enumerate(displayed_unit):
                svg.append(f'<text x="{(left + right) / 2}" y="{y + 38 + i * 17}" text-anchor="middle"><title>{escape(unit)}</title>{escape(line)}</text>')
            svg.append('</g></svg>')
            filename = f"figures/value-{metric_index}-{offset // 12 + 1}.svg"
            write_text(output_dir / filename, "\n".join(svg))
            caption = (f"{column}: {'row means' if spec['mode'] == 'observations' else 'supplied values without re-aggregation'}; "
                       f"user-declared row unit: {spec['observation_unit']}; no inferred uncertainty bars. "
                       "Full category labels, counts and omissions are in analysis.json; long labels may be shortened in the figure.")
            figures.append({"path": filename, "caption": caption, "width": spec["width"], "visual_check": "not_performed"})
    return figures


def _coordinate_figures(result: dict, output_dir: Path) -> list[dict]:
    """One metric per axis, with original point identities and explicit gaps."""
    from html import escape
    import textwrap

    spec = result["spec"]
    columns = spec["value_columns"]
    if len(columns) > spec["max_figures"]:
        raise ValueError("Selected columns exceed physical max_figures; no figures were silently dropped.")
    wide = spec["width"] == "wide"
    width, height = (720, 420) if wide else (360, 300)
    left, right, top, bottom = 75, width - 30, 40, height - 70

    def axis(values, start, end):
        low, high = min(values), max(values)
        scale = max(abs(low), abs(high), 1e-300)
        lo, hi = low / scale, high / scale
        if lo == hi:
            lo, hi = lo - .1, hi + .1
            # Avoid overflowing a constant value near the finite float limit.
            lo, hi = max(-1, lo), min(1, hi)
        def position(value):
            return start + (value / scale - lo) / (hi - lo) * (end - start)
        ticks = [(start + (end - start) * i / 2, (lo + (hi - lo) * i / 2) * scale) for i in range(3)]
        return position, ticks

    by_column = {name: [] for name in columns}
    for row in result["records"]:
        by_column[row["column"]].append(row)
    figures = []
    for index, column in enumerate(columns, start=1):
        points = by_column[column]
        if len(points) > spec["max_points"]:
            raise ValueError("Figure exceeds physical max_points; no points were sampled.")
        if spec["plot"] == "line":
            points = sorted(points, key=lambda row: row["x"])
        present = [row for row in points if row["value"] is not None]
        x, xticks = axis([row["x"] for row in points], left, right)
        y, yticks = axis([row["value"] for row in present], bottom, top)
        physical = 7 if wide else 3.5
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{physical}in" height="{height / width * physical:.4f}in" viewBox="0 0 {width} {height}">',
               f'<title>{escape(column)} versus {escape(spec["x_column"])}; supplied coordinates</title>',
               '<rect width="100%" height="100%" fill="white"/>',
               '<g font-family="sans-serif" font-size="13" fill="#203040">',
               f'<text x="8" y="22">{escape(textwrap.shorten(column, width=70 if wide else 34, placeholder="…"))}</text>',
               f'<path d="M {left} {top} V {bottom} H {right}" fill="none" stroke="#687583"/>']
        for position, value in xticks:
            svg.append(f'<text x="{position:.4f}" y="{bottom + 20}" text-anchor="middle">{value:.3g}</text>')
        for position, value in yticks:
            svg.append(f'<text x="{left - 7}" y="{position + 4:.4f}" text-anchor="end">{value:.3g}</text>')
        if spec["plot"] == "line":
            commands, connected = [], False
            for row in points:
                if row["value"] is None:
                    connected = False
                    continue
                commands.append(f'{"L" if connected else "M"} {x(row["x"]):.4f} {y(row["value"]):.4f}')
                connected = True
            svg.append(f'<path d="{" ".join(commands)}" fill="none" stroke="#286a9b" stroke-width="1.5"/>')
        for row in present:
            svg.append(f'<circle cx="{x(row["x"]):.4f}" cy="{y(row["value"]):.4f}" r="2.5" fill="#286a9b"><title>{escape(row["group"])}: x={row["x"]:.12g}, y={row["value"]:.12g}</title></circle>')
        for offset, label in enumerate((f'{spec["x_column"]} ({spec["x_unit"] or "unit not supplied"})',
                                       f'{column} ({spec["value_unit"] or "unit not supplied"})')):
            shortened = textwrap.shorten(label, width=85 if wide else 40, placeholder="…")
            svg.append(f'<text x="{(left + right) / 2}" y="{bottom + 40 + offset * 16}" text-anchor="middle"><title>{escape(label)}</title>{escape(shortened)}</text>')
        svg.append('</g></svg>')
        filename = f'figures/{spec["plot"]}-{index}.svg'
        write_text(output_dir / filename, "\n".join(svg))
        caption = (f'{column} versus {spec["x_column"]}: {spec["plot"]} of supplied coordinates; '
                   f'{len(present)} plotted, {len(points) - len(present)} missing y values; no sampling, aggregation or inferred uncertainty. '
                   + ('Ordered by numeric x; missing y values break the line. ' if spec["plot"] == "line" else 'Duplicate x coordinates are retained. ')
                   + 'Original row identities, full labels and values are in analysis.json; each value column has its own axis.')
        figures.append({"path": filename, "caption": caption, "width": spec["width"], "visual_check": "not_performed"})
    return figures
