"""Static design checks only. Does not test real devices, storage, or APIs."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import csv
import hashlib
import json
import re
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parent
passed = []


def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8-sig'))


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    passed.append(name)


def validator(name):
    schema = load(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def valid_workflow_graph(data):
    nodes = [n['id'] for n in data['nodes']]
    if len(nodes) != len(set(nodes)):
        return False
    adjacency = {n: [] for n in nodes}
    indegree = {n: 0 for n in nodes}
    for edge in data['edges']:
        a, b = edge['from'], edge['to']
        if a not in adjacency or b not in adjacency:
            return False
        adjacency[a].append(b)
        indegree[b] += 1
    queue = [n for n in nodes if indegree[n] == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for target in adjacency[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return seen == len(nodes)


pairs = [
    ('acquisition-manifest.schema.json', 'acquisition-manifest.example.json'),
    ('workflow.schema.json', 'pathology-workflow.example.json'),
    ('workflow.schema.json', 'metrics-workflow.example.json'),
    ('metrics.schema.json', 'metrics.example.json'),
]
for schema, example in pairs:
    validator(schema).validate(load(example))
    passed.append(f'JSON Schema有效且样例通过: {example}')

manifest = load('acquisition-manifest.example.json')
data = (ROOT / 'transport-fixture.bin').read_bytes()
check('合成传输文件大小与hash匹配', len(data) == manifest['files'][0]['size_bytes'] and
      hashlib.sha256(data).hexdigest() == manifest['files'][0]['sha256'])
for field, bad_value in [('sha256', 'bad'), ('size_bytes', -1)]:
    bad = deepcopy(manifest)
    bad['files'][0][field] = bad_value
    check('Schema拒绝非法' + field, bool(list(validator('acquisition-manifest.schema.json').iter_errors(bad))))
bad = deepcopy(manifest)
bad['completion_evidence']['method'] = 'stable_window_only'
check('完成清单不接受仅稳定窗口', bool(list(validator('acquisition-manifest.schema.json').iter_errors(bad))))

for name in ['pathology-workflow.example.json', 'metrics-workflow.example.json']:
    flow = load(name)
    check('流程无环且端点存在: ' + name, valid_workflow_graph(flow))
    check('流程默认关闭生产: ' + name, flow['production_enabled'] is False)
bad = load('metrics-workflow.example.json')
bad['edges'].append({'from': bad['nodes'][-1]['id'], 'to': bad['nodes'][0]['id'], 'on': 'success'})
check('图检查拒绝循环', not valid_workflow_graph(bad))
bad = load('metrics-workflow.example.json')
bad['edges'][0]['to'] = 'nonexistent'
check('图检查拒绝不存在节点', not valid_workflow_graph(bad))

spec = yaml.safe_load((ROOT / 'internal-openapi.yaml').read_text(encoding='utf-8'))
validate(spec)
passed.append('OpenAPI 3.1结构验证通过')
check('OpenAPI使用无效占位域名', '.example.invalid/' in spec['servers'][0]['url'])

fields = load('PACS_V2_字段清单_原样提取.json')
check('PACS提取16数据集288字段', len(fields) == 288 and len({r['dataset'] for r in fields}) == 16)
with (ROOT / 'PACS_V2_字段清单_原样提取.csv').open(encoding='utf-8-sig', newline='') as f:
    csv_fields = list(csv.DictReader(f))
check('CSV与JSON字段顺序一致', [(r['dataset'], r['field']) for r in fields] ==
      [(r['dataset'], r['field']) for r in csv_fields])
check('保留原文特殊字段拼写', {'acquistionTime', 'bookDpetName', 'fixhospitalCode', 'fixHospitalCode'} <=
      {r['field'] for r in fields})

for source in json.loads((PACKAGE / '资料提取/来源文件摘要.json').read_text(encoding='utf-8')):
    p = PACKAGE.parent / source['file']
    check('原始文件未改: ' + p.name, hashlib.sha256(p.read_bytes()).hexdigest() == source['sha256'])

docs = [*PACKAGE.glob('*.md'), ROOT / 'README.md', PACKAGE.parent / '医院数据归集与上报平台总体方案_v0.3.md',
        PACKAGE.parent / '00_AI复核入口.md', PACKAGE.parent / '归档/2026-09-30/README.md']
for p in docs:
    text = p.read_text(encoding='utf-8')
    check('Markdown围栏/字符正常: ' + p.name, text.count('```') % 2 == 0 and '\ufffd' not in text)
    for target in re.findall(r'\]\(([^)]+)\)', text):
        if not target.startswith(('http://', 'https://', '#')):
            check('本地链接存在: ' + target, (p.parent / target.split('#')[0]).exists())

sample = '111-110101199004051841-2-7944928522-88390329568976389053-20260130083055-H11010100XXX-1'
check('正式索引示例79/86长度核对', len(sample.replace('-', '')) == 79 and len(sample) == 86)
check('PACS草案不启用生产', load('destination-profile.example.json')['production_enabled'] is False)

executed_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
report = f'# 设计交付静态校验记录\n\n执行时间（UTC+08:00）：{executed_at}。\n\n'
report += f'通过 {len(passed)} 项静态检查，无失败。\n\n'
report += '\n'.join('- ' + item for item in passed)
report += '\n\n范围限制：以上仅验证设计文件、Schema、样例、OpenAPI结构、DAG基本形态、来源完整性和本地链接；未验证运行实现、字段映射业务正确性、厂商能力、医院网络、真实图像与医保平台。完整系统仍需按07验收。\n'
(PACKAGE / '08_设计交付静态校验记录.md').write_text(report, encoding='utf-8')
print(f'PASS: {len(passed)} checks. Report: {PACKAGE / "08_设计交付静态校验记录.md"}')
