"""MON-01: every time-series graph uses the shared Cacti/Zabbix-style renderer (static/rrd_chart.js)."""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def main():
    renderer = (STATIC / "rrd_chart.js").read_text(encoding="utf-8")
    for feature in ("window.NSMChart", "renderLegend", "'Attuale', 'Min', 'Media', 'Max'", "segments(", "STEPS", "rrd-threshold", "telemetry-tooltip"):
        assert feature in renderer, feature
    assert "innerHTML = '<" not in renderer and ".style.cssText" not in renderer, "CSP: no HTML strings or inline style blocks"
    for name in ("telemetry_monitor.js", "traffic_monitor.js", "series_chart.js"):
        script = (STATIC / name).read_text(encoding="utf-8")
        assert "window.NSMChart.render(" in script, name
        assert "createElementNS" not in script, f"{name} must not draw its own SVG"
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    for rule in (".rrd-area.rrd-a", ".rrd-legend-table", ".rrd-grid", ".rrd-key.rrd-d::before"):
        assert rule in css, rule
    print("Graphs style smoke passed")


if __name__ == "__main__":
    main()
