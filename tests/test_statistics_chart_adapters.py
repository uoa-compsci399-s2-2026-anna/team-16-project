"""Runtime contracts for the public statistics chart adapters.

The real vendored runtime is loaded first, then only its canvas-bound Chart
constructor is replaced. This checks the real adapter without needing layout.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _probe(body: str) -> None:
    if not shutil.which("node"):
        pytest.skip("Node is required for the chart adapter runtime contract")
    prelude = f"""
import assert from 'node:assert/strict';
import {{readFileSync}} from 'node:fs';
await import({json.dumps((WEB / 'vendor/chart.umd.min.js').as_uri())});
class RecordingChart {{
  static calls = [];
  static helpers = {{toFont: () => ({{string: '12px sans-serif'}})}};
  constructor(canvas, config) {{
    this.canvas = canvas;
    this.config = config;
    RecordingChart.calls.push(config);
  }}
}}
globalThis.Chart = RecordingChart;
const source = readFileSync(new URL({json.dumps((WEB / 'js/charts.js').as_uri())}), 'utf8')
  .replace("import '../vendor/chart.umd.min.js'", '');
const module = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const canvas = {{}};
const config = chart => chart.config;
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", prelude + body],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_chart_namespace_preserves_main_exports_and_adds_pie_line():
    _probe("""
assert.deepEqual(Object.keys(module).sort(),
  ['PALETTE', 'renderBar', 'renderDonut', 'renderLine', 'renderPie']);
for (const name of ['renderBar', 'renderDonut', 'renderLine', 'renderPie'])
  assert.equal(typeof module[name], 'function');
assert.equal(module.PALETTE.length, 16);
""")


def test_stats_module_imports_from_its_real_file_url_with_chart_renderers_available():
    """A missing named chart import must fail during module evaluation, before page setup."""
    if not shutil.which("node"):
        pytest.skip("Node is required for the statistics module import contract")
    script = f"""
import assert from 'node:assert/strict';
globalThis.Chart = class Chart {{}};
globalThis.window = {{
  location: {{search: ''}},
  addEventListener() {{}},
}};
globalThis.document = {{
  documentElement: {{}},
  addEventListener() {{}},
  getElementById() {{ return null; }},
  querySelector() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
const module = await import({json.dumps((WEB / 'js/stats.js').as_uri())});
assert.deepEqual(Object.keys(module).sort(), [
  'destroyCharts', 'loadStats', 'renderStats', 'renderStatsError',
  'rerenderInActiveLanguage',
]);
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("values", [[-1], ["-0.25", "0.75"], [0, "-3"]])
def test_pie_rejects_finite_negative_values_before_chart_creation(values):
    _probe(f"""
const before = RecordingChart.calls.length;
assert.throws(() => module.renderPie(canvas,
  {json.dumps([{'label': str(i), 'share': value} for i, value in enumerate(values)])}),
  error => error instanceof RangeError && error.message === 'Pie charts require non-negative values');
assert.equal(RecordingChart.calls.length, before);
""")


def test_pie_keeps_zero_and_invalid_values_without_throwing():
    _probe("""
const values = [0, -0, '0.25', null, '', NaN, Infinity, {}];
const rows = values.map((share, i) => ({label: String(i), share}));
const chart = module.renderPie(canvas, rows);
assert.deepEqual(config(chart).data.datasets[0].data,
  [0, -0, 0.25, null, null, null, null, null]);
assert.equal(RecordingChart.calls.length, 1);
""")


def test_renderers_preserve_values_and_formatter_contract():
    _probe("""
const rows = [12, -5, '0.379'].map((value, i) => ({label: `L${i}`, value}));
for (const name of ['renderBar', 'renderLine']) {
  const chart = module[name](canvas, rows, {formatValue: v => `F:${v}`});
  const c = config(chart);
  assert.deepEqual(c.data.datasets[0].data, [12, -5, 0.379]);
  assert.equal(c.options.scales.y.ticks.callback(-5), 'F:-5');
  assert.equal(c.options.plugins.tooltip.callbacks.label({label: 'L2', parsed: {y: 0.379}}),
    'L2: F:0.379');
  const plain = config(module[name](canvas, rows));
  assert.equal(plain.options.scales.y.ticks.callback(-5), '-5');
  assert.equal(plain.options.plugins.tooltip.callbacks.label({label: 'L2', parsed: {y: 0.379}}),
    'L2: 0.379');
}
assert.equal(config(module.renderBar(canvas, rows)).options.scales.y.suggestedMin, -5);
const nonnegativeHint = config(module.renderBar(canvas, rows, {allowNegative: false}));
assert.equal(nonnegativeHint.options.scales.y.suggestedMin, 0);
assert.deepEqual(nonnegativeHint.data.datasets[0].data, [12, -5, 0.379]);
const pie = config(module.renderPie(canvas, [{label: 'Part', share: '0.379'}]));
const donut = config(module.renderDonut(canvas, [{label: 'Part', share: '0.379'}]));
assert.equal(pie.type, 'pie');
assert.equal(donut.type, 'doughnut');
assert.equal(donut.options.cutout, '58%');
for (const name of ['renderPie', 'renderDonut', 'renderBar', 'renderLine']) {
  const c = config(module[name](canvas, [{label: 'Part', share: '0.379'}], {
    valueKey: 'share', formatValue: v => `${new Intl.NumberFormat('en-NZ', {
      style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1,
    }).format(v)}`,
  }));
  const parsed = name === 'renderPie' || name === 'renderDonut'
    ? {label: 'Part', parsed: 0.379} : {label: 'Part', parsed: {y: 0.379}};
  assert.equal(c.options.plugins.tooltip.callbacks.label(parsed), 'Part: 37.9%');
  if (name === 'renderBar' || name === 'renderLine')
    assert.equal(c.options.scales.y.ticks.callback(0.379), '37.9%');
}
""")


def test_bar_and_line_use_blueberry_for_series_and_tooltip():
    _probe("""
const rows = [{label: 'Part', value: 1}];
for (const name of ['renderBar', 'renderLine']) {
  const c = config(module[name](canvas, rows));
  const dataset = c.data.datasets[0];
  assert.equal(dataset.backgroundColor, '#005AE6', `${name} series fill`);
  assert.equal(c.options.plugins.tooltip.backgroundColor, '#005AE6', `${name} tooltip fill`);
  assert.equal(c.options.plugins.tooltip.titleColor, '#FFFFFF', `${name} tooltip title`);
  assert.equal(c.options.plugins.tooltip.bodyColor, '#FFFFFF', `${name} tooltip body`);
  if (name === 'renderLine') {
    assert.equal(dataset.borderColor, '#005AE6', 'renderLine stroke');
    assert.equal(dataset.pointBackgroundColor, '#005AE6', 'renderLine points');
  }
}
""")


def test_pie_brand_colours_are_stable_and_do_not_drop_buckets():
    _probe("""
const rows = Array.from({length: 16}, (_, i) => ({code: `code-${i}`, label: `Label ${i}`, share: 1}));
const fills = c => c.data.datasets[0].backgroundColor;
const first = config(module.renderPie(canvas, rows));
assert.equal(new Set(fills(first)).size, 16);
const byCode = new Map(rows.map((r, i) => [r.code, fills(first)[i]]));
const reversed = config(module.renderPie(canvas, [...rows].reverse()));
for (let i = 0; i < rows.length; i++)
  assert.equal(fills(reversed)[i], byCode.get(rows[15-i].code));
const renamed = config(module.renderPie(canvas, rows.map(r => ({...r, label: `Changed ${r.code}`}))));
assert.deepEqual(fills(renamed), fills(first));
const collision = config(module.renderPie(canvas, [
  {label: 'label-19032', share: 1}, {label: 'label-43502', share: 1},
]));
assert.notEqual(fills(collision)[0], fills(collision)[1]);
const extra = config(module.renderPie(canvas, [
  ...rows, {code: 'other', label: 'Other', share: 1},
]));
assert.equal(extra.data.labels.length, 17);
assert.equal(extra.data.datasets[0].data.length, 17);
assert.equal(fills(extra).length, 17);
const allowed = new Map(module.PALETTE.map(entry => [entry.fill, entry.ink]));
for (let i = 0; i < fills(extra).length; i++) {
  assert.ok(allowed.has(fills(extra)[i]));
  const context = {tooltip: {dataPoints: [{dataIndex: i}]}};
  assert.equal(extra.options.plugins.tooltip.backgroundColor(context), fills(extra)[i]);
  assert.equal(extra.options.plugins.tooltip.titleColor(context), allowed.get(fills(extra)[i]));
  assert.equal(extra.options.plugins.tooltip.bodyColor(context), allowed.get(fills(extra)[i]));
}
const special = config(module.renderPie(canvas, [
  {label: 'Other', share: 1}, {label: 'Unspecified', share: 2},
]));
assert.deepEqual(special.data.labels, ['Other', 'Unspecified']);
assert.deepEqual(special.data.datasets[0].data, [1, 2]);
""")


@pytest.mark.parametrize("renderer", ["renderDonut", "renderPie", "renderBar", "renderLine"])
@pytest.mark.parametrize("matches", [False, True])
def test_new_charts_preserve_legend_fitting_and_reduced_motion(renderer, matches):
    _probe(f"""
const queries = [];
globalThis.matchMedia = query => {{
  queries.push(query);
  return {{matches: query === '(prefers-reduced-motion: reduce)' && {str(matches).lower()}}};
}};
const c = config(module[{json.dumps(renderer)}](canvas, [{{label: 'Part', share: 1, value: -1}}]));
assert.deepEqual(queries, ['(prefers-reduced-motion: reduce)']);
assert.equal(c.options.animation === false, {str(matches).lower()});
assert.ok(c.plugins.some(plugin => plugin.id === 'kaiFitLegend'));
if ({json.dumps(renderer)} === 'renderPie')
  assert.equal(typeof c.options.plugins.legend.labels.generateLabels, 'function');
if ({json.dumps(renderer)} === 'renderLine') {{
  assert.equal(c.options.scales.x.type, 'category');
  assert.equal(c.data.datasets[0].tension, 0);
  assert.equal(c.data.datasets[0].fill, false);
  assert.equal(c.data.datasets[0].spanGaps, false);
  assert.deepEqual(c.data.datasets[0].data, [-1]);
}}
""")


def test_renderers_work_without_match_media():
    _probe("""
delete globalThis.matchMedia;
for (const name of ['renderDonut', 'renderPie', 'renderBar', 'renderLine']) {
  const c = config(module[name](canvas, [{label: 'Part', share: 1, value: 1}]));
  assert.notEqual(c.options.animation, false);
}
""")
