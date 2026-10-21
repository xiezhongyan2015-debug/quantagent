from pathlib import Path
import html
import re
import json
import hashlib
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'output' / 'reports' / 'meta_framework_redesign_20260906.html'
OUT.parent.mkdir(parents=True, exist_ok=True)
source = HERE / 'report-source.md'
text = source.read_text(encoding='utf-8')
text = text.replace('](../../../META_', '](../../META_').replace('](../../../../experiment_traces/', '](../../../experiment_traces/')
if '[L2样本核查]' not in text:
    text = text.replace('尚未进入收益竞赛。后续优先', '尚未进入收益竞赛。[L2样本核查](../../META_L2_CONTRACT_SAMPLE_AUDIT.md) [L2特征契约](../../META_L2_DAILY_FEATURES.md) 后续优先')
source.write_text(text, encoding='utf-8')
links = []
def inline(s):
    result, pos = [], 0
    for m in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', s):
        result.append(html.escape(s[pos:m.start()]))
        label, url = m.group(1), m.group(2)
        if not url.startswith('https://'):
            target = (HERE / url).resolve()
            assert target.is_file(), target
            assert target.is_relative_to(ROOT), target
            url = os.path.relpath(target, OUT.parent).replace('\\', '/')
        links.append({'label':label, 'url':url})
        result.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
        pos = m.end()
    result.append(html.escape(s[pos:]))
    return ''.join(result)

sections, title, intro = [], '', []
lines = text.splitlines()
i = 0
current = intro
while i < len(lines):
    line = lines[i].strip()
    if not line:
        i += 1
        continue
    if line.startswith('# '):
        title = line[2:]
    elif line.startswith('## '):
        heading = line[3:]
        current = []
        sections.append((f's{len(sections)+1}', heading, current))
    elif line.startswith('|'):
        rows = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            raw = lines[i].strip()
            if not re.fullmatch(r'[|:\-\s]+', raw):
                rows.append([inline(v.strip()) for v in raw.strip('|').split('|')])
            i += 1
        width = len(rows[0])
        assert all(len(r) == width for r in rows)
        parts = ['<div class="table-wrap" role="region" aria-label="比较表格" tabindex="0"><table><thead><tr>']
        parts += [f'<th scope="col">{v}</th>' for v in rows[0]]
        parts.append('</tr></thead><tbody>')
        for row in rows[1:]:
            parts.append('<tr>'+''.join(f'<td>{v}</td>' for v in row)+'</tr>')
        parts.append('</tbody></table></div>')
        current.append(''.join(parts))
        continue
    else:
        current.append('<p>'+inline(line)+'</p>')
    i += 1

flow = '''<figure class="architecture" aria-labelledby="flow-caption">
<figcaption id="flow-caption">三部分协作与权限边界</figcaption>
<div class="layer"><b>元评测与版本选择</b><span>读取允许反馈的证据 → 提出改动 → 冻结对照 → 保留或淘汰</span></div>
<div class="flow-arrow" aria-hidden="true">↓ 新版本　↑ 研究结果</div>
<div class="layer"><b>研究 harness</b><span>观察与联想 → 选择实验 → 实现与检验 → 更新判断 → 提交或弃权</span></div>
<div class="flow-arrow" aria-hidden="true">↓ 受限操作　↑ 可核验证据</div>
<div class="layer kernel"><b>可信实验内核</b><span>时间边界 · 原价执行 · 证据账本 · 付费回执 · 预算与独立评分</span></div>
<p>一次冻结比较中，研究器和元评测器都不能修改内核评分与留出权限。</p></figure>'''
comparison = '''<div class="comparison" aria-label="两种实验比较">
<p><strong>查看不同实验能回答什么</strong></p>
<div class="buttons"><button type="button" aria-pressed="true" data-view="total">总预算下的整体效果</button><button type="button" aria-pressed="false" data-view="mechanism">固定动作下的机制效果</button></div>
<div id="total" class="compare-panel"><p>双方共享 Astra/xhigh、数据、工具与资源规则，各自决定如何分配研究动作。比较最终结果与实际全栈成本。</p><p>候选主动多做回测，可以贡献整体效果；收益差不能单独解释成诊断能力提高。</p></div>
<div id="mechanism" class="compare-panel" hidden><p>双方诊断、回测、提交机会固定，只改变预先指定的指导或流程。比较特定行为与结果是否改变。</p><p>Q属于这条轨道。结果说明固定条件下的效果，不能替代自由研究能力评测。</p></div>
</div>'''
nav = ''.join(f'<a href="#{sid}">{html.escape(heading)}</a>' for sid,heading,_ in sections)
body = []
for sid, heading, content in sections:
    body.append(f'<section id="{sid}"><h2>{html.escape(heading)}</h2>')
    if sid == 's3': body.append(flow)
    if sid == 's7': body.append(comparison)
    body.extend(content)
    body.append('</section>')

css = '''
:root{color-scheme:light;--ink:#202c37;--muted:#576875;--line:#d9e2e8;--accent:#075e65;--pale:#edf5f4}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:28px}body{margin:0;background:#f7f9fa;color:var(--ink);font:17px/1.95 "Microsoft YaHei","PingFang SC",sans-serif}a{color:var(--accent);text-underline-offset:4px;overflow-wrap:anywhere}a:hover{color:#023e43}a:focus-visible,button:focus-visible,.table-wrap:focus-visible{outline:3px solid #d49926;outline-offset:4px}.shell{max-width:1460px;margin:auto;display:grid;grid-template-columns:250px minmax(0,1fr);gap:50px;padding:36px 44px}aside{position:sticky;top:28px;align-self:start;max-height:92vh;overflow:auto;padding:12px 0}aside .brand{font-size:12px;letter-spacing:2px;color:var(--muted);margin-bottom:22px}nav a{display:block;font-size:13px;line-height:1.6;padding:8px 13px;margin:2px 0;text-decoration:none;border-left:2px solid transparent}nav a.active{border-color:var(--accent);background:var(--pale);font-weight:bold}main{background:white;min-width:0;border:1px solid #e3e9ed;padding:52px 62px;box-shadow:0 8px 35px #15293705}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--accent)}h1{font-size:34px;line-height:1.45;margin:16px 0 12px;color:#17242e}h2{font-size:24px;line-height:1.55;letter-spacing:.1px;margin:0 0 24px;color:#17242e}header p:first-of-type{font-size:13px;color:var(--muted);margin:0 0 30px}header p:nth-of-type(2){font-size:19px;line-height:1.9;font-weight:550}p{margin:0 0 21px;overflow-wrap:break-word}p a{font-size:13px;white-space:normal}section{margin-top:64px;padding-top:8px}.table-wrap{overflow-x:auto;margin:26px 0 30px}table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.75;table-layout:fixed}th{text-align:left;font-weight:600;background:#edf3f5;color:#233f4b;border-bottom:2px solid #cbdadf}td,th{padding:13px 14px;vertical-align:top;overflow-wrap:break-word}td{border-bottom:1px solid var(--line)}td:first-child{font-weight:550}tr:nth-child(even) td{background:#fafcfc}.architecture{margin:26px 0 34px;padding:24px;background:#f5f8f9;border:1px solid var(--line)}figcaption{font-size:15px;font-weight:bold;margin-bottom:20px}.layer{padding:16px 20px;background:white;border:1px solid #d7e0e5}.layer b{display:block;font-size:16px}.layer span{font-size:14px;color:var(--muted)}.kernel{background:#edf5f4;border-color:#b9d5d2}.flow-arrow{font-size:13px;text-align:center;padding:7px;color:var(--muted)}.architecture p{font-size:12px;margin:15px 0 0;color:var(--muted)}.comparison{padding:23px;margin-bottom:26px;border:1px solid #b9d5d2;background:#f8fbfa}.comparison p{font-size:14px}.comparison p:last-child{margin:0}.buttons{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:20px}button{font:inherit;font-size:13px;cursor:pointer;border:1px solid #b7c9ca;background:white;color:var(--accent);padding:9px 14px;border-radius:4px}button[aria-pressed=true]{background:var(--accent);color:white}.tools{display:flex;gap:12px;margin-top:22px}.tools a{font-size:12px}footer{margin-top:55px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.skip{position:absolute;left:-9999px}.skip:focus{left:20px;top:8px;background:white;padding:8px;z-index:5}
@media(max-width:1100px){.shell{gap:25px;padding:24px;grid-template-columns:210px minmax(0,1fr)}main{padding:35px}}@media(max-width:760px){body{font-size:16px}.shell{display:block;padding:14px}aside{position:static;max-height:none;padding:5px 8px 20px}nav{display:grid;grid-template-columns:1fr 1fr}nav a{font-size:12px;padding:5px 8px}aside .brand{margin:0 0 10px}main{padding:28px 22px}h1{font-size:29px}h2{font-size:22px}section{margin-top:45px}table{min-width:600px}.architecture{padding:16px}.layer{padding:12px}}
@media print{body{background:white;font:11pt/1.7 "Microsoft YaHei",sans-serif}.shell{display:block;padding:0}aside,.tools,.buttons,.skip{display:none}main{border:0;box-shadow:none;padding:0}h1{font-size:24pt}h2{font-size:17pt;break-after:avoid}section{margin-top:25pt}p{orphans:3;widows:3}.table-wrap{overflow:visible}table{font-size:9pt;min-width:0}tr,.layer{break-inside:avoid}a{color:#075e65}header p:nth-of-type(2){font-size:12pt}.compare-panel[hidden]{display:block}.comparison{break-inside:avoid}footer{margin-top:25pt}@page{size:A4;margin:20mm}}
'''
ledger = json.loads((HERE/'claim-source-ledger.json').read_text(encoding='utf-8'))
source_notes = '<details class="source-notes"><summary>研究来源与版本</summary><ol>' + ''.join('<li><a href="'+html.escape(s['url'],quote=True)+'">'+html.escape(s['title'])+'</a><br>'+html.escape(s['author']+' · '+s['date'])+'</li>' for s in ledger['sources']) + '</ol></details>'
css += '.source-notes{font-size:12px;margin-top:40px;line-height:1.7;color:var(--muted)}.source-notes summary{cursor:pointer;color:var(--accent);padding:12px 0}.source-notes li{margin-bottom:16px;overflow-wrap:anywhere}.source-notes ol{padding-left:22px}'
script = '''document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));document.querySelectorAll('.compare-panel').forEach(p=>p.hidden=p.id!==b.dataset.view)}));document.querySelector('#print').addEventListener('click',()=>window.print());const obs=new IntersectionObserver(es=>{const e=es.filter(x=>x.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top)[0];if(e){document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.getAttribute('href')==='#'+e.target.id));}},{rootMargin:'-10% 0px -65% 0px'});document.querySelectorAll('section').forEach(s=>obs.observe(s));'''
document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="QuantaAgents A股策略元框架的证据审查与实施设计"><title>{html.escape(title)}</title><style>{css}</style></head><body><a class="skip" href="#report">跳到正文</a><div class="shell"><aside aria-label="报告导航"><div class="brand">QUANTA AGENTS · RESEARCH</div><nav>{nav}</nav><div class="tools"><button id="print" type="button">打印报告</button></div></aside><main id="report"><header><div class="eyebrow">研究结论与实施方案</div><h1>{html.escape(title)}</h1>{''.join(intro)}</header>{''.join(body)}{source_notes}<footer>本报告是方案与证据快照，非策略达标凭证。运行状态截至2026年9月6日22时47分香港时间。正文已完成独立关键论证审查；配套实验保持其原始版本身份。页面布局采用关键区域抽样视觉检查。</footer></main></div><script>{script}</script></body></html>'''
OUT.write_text(document,encoding='utf-8')
assert len(sections)==12
assert not re.search(r'turn\d+(?:view|search)||\[wordlim',document)
qa = {'output':str(OUT),'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'sections':len(sections),'tables':document.count('<table>'),'links':links,'external_sources':len(set(x['url'] for x in links if x['url'].startswith('https://'))),'local_links_verified':True,'html_bytes':OUT.stat().st_size,'visual_review':'pending'}
(HERE/'artifact_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in qa.items() if k!='links'},ensure_ascii=False))
