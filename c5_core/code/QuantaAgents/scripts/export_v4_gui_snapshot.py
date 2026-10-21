"""Export the saved closed V4 cohort as one offline HTML file, without a server.

Reads exactly the configured display JSON and its saved nine-slot summary.
No ledger, browser, timer, network request or research worker is invoked.
The output must be a new .html file; original evidence is never modified.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / 'docs/research/meta_framework_v4_handoff/gui_status_001.json'
SLOTS = ['v1', 's1', 'f1', 's2', 'f2', 'v2', 'f3', 'v3', 's3']
MAX_SOURCE_BYTES = 4 * 1024**2


def text(value):
    return escape(str(value), quote=True)


def read_source(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file() or path.is_symlink():
        raise ValueError('Saved display source is missing or redirected')
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('Saved display source exceeds the export bound')
    content = path.read_bytes()
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError('Saved display source exceeds the export bound')
    value = json.loads(content.decode('utf-8-sig'))
    return value, {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(),
                   'bytes': len(content), 'json_text': content.decode('utf-8-sig')}


def render(status, summary, sources, *, saved_at):
    """Pure rendering; tests may supply generated JSON without opening sources."""
    if (status.get('study_id') != 'v4s1' or summary.get('study_id') != 'v4s1'
            or status.get('cohort_status') != 'closed_audited'
            or summary.get('cohort_status') != 'closed_audited'
            or status.get('formal_target_success') is not False
            or summary.get('formal_target_success') is not False
            or summary.get('formal_success_denominator_contribution') != 0):
        raise ValueError('This offline export requires the saved closed, non-formal v4s1 scope')
    stages = status.get('stages')
    if (type(stages) is not list or len(stages) != 3
            or [s.get('id') for s in stages] != ['engineering', 'real_research', 'formal_validation']):
        raise ValueError('All three original display stages are required')
    columns, rows = summary.get('columns'), summary.get('rows')
    if (type(columns) is not list or len(columns) != 8 or type(rows) is not list
            or len(rows) != len(SLOTS) or any(type(r) is not list or len(r) != 8 for r in rows)
            or [r[0] for r in rows] != SLOTS):
        raise ValueError('All nine original slots, in their saved order, are required')
    if saved_at.tzinfo is None:
        raise ValueError('The export timestamp must contain a timezone')
    stamp = saved_at.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S 香港时间')
    cards = []
    for stage in stages:
        details = ''.join('<li>' + text(item) + '</li>' for item in stage.get('details', []))
        cards.append('<section class="card"><h2>' + text(stage['title']) + '</h2>'
            '<p class="state">' + text(stage['status']) + '</p><p>' + text(stage['summary'])
            + '</p><ul>' + details + '</ul><p class="gap">当前缺口：'
            + text(stage.get('gap', '未报告')) + '</p></section>')
    heading = ''.join('<th scope="col">' + text(c) + '</th>' for c in columns)
    table_rows = ''.join('<tr>' + ''.join(
        ('<th scope="row">' + text(cell) + '</th>') if i == 0 else '<td>' + text(cell) + '</td>'
        for i, cell in enumerate(row)) + '</tr>' for row in rows)
    facts = ''.join('<li>' + text(item) + '</li>' for item in status.get('latest_progress_facts', []))
    provenance = []
    for source in sources:
        provenance.append('<section class="source"><h3>' + text(source['label']) + '</h3>'
            '<dl><dt>本地路径</dt><dd>' + text(source['path']) + '</dd>'
            '<dt>SHA256</dt><dd><code>' + text(source['sha256']) + '</code></dd>'
            '<dt>文件字节</dt><dd>' + text(source['bytes']) + '</dd></dl>'
            '<details><summary>查看保存的原始 JSON</summary><pre>' + text(source['json_text'])
            + '</pre></details></section>')
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>QuantaAgents V4 监管快照</title>
<style>
:root{color-scheme:light;font-family:system-ui,"Microsoft YaHei",sans-serif;color:#172334;background:#eaf0f6}
*{box-sizing:border-box}body{margin:0;line-height:1.65}header{background:#14243b;color:#fff;padding:30px max(24px,calc((100% - 1480px)/2))}
h1{margin:0 0 10px;font-size:30px}h2{font-size:21px;margin:0 0 12px}h3{font-size:17px;margin:0 0 10px}
p{margin:10px 0}.subtle{color:#516076;font-size:14px}header .subtle{color:#d1dceb}.tags{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.tag{padding:4px 12px;border:1px solid #849ab8;border-radius:20px;font-weight:600}.tag.warn{background:#644a20;border-color:#d6b66d}
main{max-width:1530px;margin:auto;padding:24px}.notice{background:#fff6df;border-left:4px solid #b88622;padding:16px 20px;margin:0 0 22px}
.stages{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.card,.panel{background:#fff;border:1px solid #d7e0eb;border-radius:12px;padding:22px;box-shadow:0 2px 8px #10253e08}
.card{border-top:4px solid #426d9f}.state{font-size:19px;font-weight:700;color:#245281}.gap{background:#f5f7fa;border-left:3px solid #9e6e23;padding:10px 12px}
ul{padding-left:22px}li+li{margin-top:7px}.panel{margin-top:22px}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:1080px;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:12px 10px;border-bottom:1px solid #d7e0eb}thead{background:#edf2f8}tbody tr:nth-child(even){background:#f7f9fc}th:first-child{width:60px}td:last-child{min-width:270px}
.source{padding:16px 0;border-top:1px solid #d7e0eb}.source:first-of-type{border-top:0}dl{display:grid;grid-template-columns:100px 1fr;margin:0;gap:7px 12px}dt{color:#516076}dd{margin:0;overflow-wrap:anywhere}code,pre{font-family:Consolas,monospace;font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;padding:16px;max-height:480px;overflow:auto}details{margin-top:12px}summary{cursor:pointer;color:#245281}footer{color:#516076;margin:22px 0;font-size:13px}
@media(max-width:980px){.stages{grid-template-columns:1fr}main{padding:16px}.card,.panel{padding:18px}h1{font-size:25px}dl{grid-template-columns:1fr;gap:2px}}
@media print{header{background:#fff;color:#172334;padding:15px}.tag{border-color:#777}header .subtle{color:#516076}main{padding:0}.card,.panel{box-shadow:none;break-inside:avoid}.stages{grid-template-columns:1fr}table{min-width:0;font-size:10px}.table-wrap{overflow:visible}td:last-child{min-width:0}details{display:none}}
</style></head><body><header><h1>QuantaAgents V4 监管快照</h1>
<p>工程完善、原研究结果与正式验证分开展示。</p>
<p class="subtle">HTML 保存时间：''' + text(stamp) + '''</p>
<div class="tags"><span class="tag">静态快照</span><span class="tag warn">实时服务不在线</span>
<span class="tag">原 cohort 已关闭</span><span class="tag warn">正式验证未启动</span></div></header>
<main><p class="notice">这是本地保存的监管页面，不会自动刷新。当前交付时实时监管服务不在线；页面显示已保存的结果，不表示研究进程正在运行。正式成功分母贡献仍为 0。</p>
<p class="subtle">阶段状态保存时间：''' + text(status.get('observed_at', '未提供')) + '''<br>九槽摘要保存时间：''' + text(summary.get('observed_at', '未提供')) + '''</p>
<div class="stages">''' + ''.join(cards) + '''</div>
<section class="panel"><h2>当前推进</h2><p>''' + text(status.get('next_action', '未报告')) + '''</p><ul>''' + facts + '''</ul></section>
<section class="panel"><h2>完整九槽记录</h2><p>''' + text(summary.get('summary', '')) + '''</p>
<div class="table-wrap"><table><caption class="subtle">原槽位全部保留；未启动、交付失败、弃权与未知结果分别记载。</caption><thead><tr>''' + heading + '''</tr></thead><tbody>''' + table_rows + '''</tbody></table></div>
<p class="gap">''' + text(summary.get('limitations', '')) + '''</p></section>
<section class="panel"><h2>来源与可核对记录</h2><p class="subtle">以下为导出时读取的两份原始 JSON，路径和 SHA256 对应原文件字节。原 JSON 中较早的服务状态描述属于历史保存内容，当前页面顶部已明确服务离线；这些来源不是新的正式验收。</p>''' + ''.join(provenance) + '''</section>
<footer>本导出只生成这一份 HTML；原账、原研究、候选与费用记录均未修改。没有启动服务、研究任务或自动监控。</footer>
</main></body></html>'''


def export(output):
    output = Path(output).absolute()
    if output.suffix.lower() != '.html' or output.exists() or output.parent.resolve() != output.parent:
        raise ValueError('Choose a new .html output under an existing, unredirected directory')
    if not output.parent.is_dir():
        raise ValueError('Output directory must already exist')
    status, status_source = read_source(STATUS)
    summary_path = Path(status['cycle_results_summary_path']).absolute()
    if summary_path.parent != ROOT / 'experiment_traces/v4s1':
        raise ValueError('Summary must be a saved JSON in the original v4s1 directory')
    summary, summary_source = read_source(summary_path)
    status_source['label'] = '阶段状态 JSON'
    summary_source['label'] = '完整九槽摘要 JSON'
    sources = [status_source, summary_source]
    saved_at = datetime.now(timezone.utc)
    html = render(status, summary, sources, saved_at=saved_at)
    for source in sources:
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Saved display source changed during export; no HTML written')
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(html)
    return {'output': str(output), 'saved_at': saved_at.isoformat(), 'static_snapshot': True,
            'new_model_calls': 0, 'formal_target_success': False,
            'source_sha256': {source['path']: source['sha256'] for source in sources},
            'html_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='A new local .html file; existing files are rejected')
    args = parser.parse_args()
    print(json.dumps(export(args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
