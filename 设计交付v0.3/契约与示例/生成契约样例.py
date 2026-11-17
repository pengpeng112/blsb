"""Regenerate design contracts; not application implementation or external protocol."""
from pathlib import Path
import hashlib
import json
import yaml

ROOT = Path(__file__).resolve().parent
BASE = 'https://schemas.example.invalid/reporting/'


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def obj(properties, required=(), **extras):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': list(required), **extras}


def arr(items, minimum=0):
    return {'type': 'array', 'items': items, 'minItems': minimum}


def string(**extras):
    return {'type': 'string', 'minLength': 1, **extras}


UUID = string(format='uuid')
HASH = string(pattern='^[a-f0-9]{64}$')
TIME = string(format='date-time')
POSITIVE = {'type': 'integer', 'minimum': 1}
NONNEG = {'type': 'integer', 'minimum': 0}
SCHEMA_REF = obj({'key': string(), 'version': string()}, ['key', 'version'])

member = obj({
    'client_asset_id': UUID, 'relative_path': string(maxLength=1024),
    'role': {'enum': ['primary', 'index', 'tile_data', 'label', 'thumbnail', 'metadata', 'report', 'other']},
    'size_bytes': NONNEG, 'sha256': HASH, 'media_type': string(),
    'format_hint': string(),
}, ['client_asset_id', 'relative_path', 'role', 'size_bytes', 'sha256'])
manifest = obj({
    'schema_version': {'const': '1.0'}, 'event_id': UUID, 'agent_id': UUID,
    'source_system_code': string(), 'root_id': string(), 'captured_at': TIME,
    'source_scan_id': string(), 'source_revision': string(),
    'identity_hints': obj({'pathology_no': string(), 'slide_id': string(), 'barcode_raw': string()}),
    'completion_evidence': obj({
        'method': {'enum': ['vendor_api', 'vendor_manifest', 'manual_confirmation']},
        'confirmed_at': TIME, 'evidence_ref': string(),
        'source_immutable_assertion': {'type': 'boolean'},
    }, ['method', 'confirmed_at', 'evidence_ref', 'source_immutable_assertion']),
    'files': arr(member, 1),
}, ['schema_version', 'event_id', 'agent_id', 'source_system_code', 'root_id',
    'captured_at', 'source_scan_id', 'source_revision', 'completion_evidence', 'files'])
manifest.update({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                 '$id': BASE + 'acquisition-manifest.schema.json',
                 'description': '我方拟议内部协议；服务端还须验证路径、重复ID、权限和完成证据。启发式候选不能直接提交为完成清单。'})
save('acquisition-manifest.schema.json', manifest)

sample_manifest = {
    'schema_version': '1.0', 'event_id': '00000000-0000-4000-8000-000000000001',
    'agent_id': '00000000-0000-4000-8000-000000000002',
    'source_system_code': 'scanner-simulator', 'root_id': 'test-output',
    'captured_at': '2026-09-29T09:00:00+08:00', 'source_scan_id': 'scan-demo-1',
    'source_revision': '1', 'identity_hints': {'pathology_no': 'P-DEMO-001', 'slide_id': 'S-DEMO-001'},
    'completion_evidence': {'method': 'vendor_manifest', 'confirmed_at': '2026-09-29T09:00:00+08:00',
                            'evidence_ref': 'simulator:scan-demo-1:revision-1', 'source_immutable_assertion': True},
    'files': [{'client_asset_id': '00000000-0000-4000-8000-000000000003',
               'relative_path': 'scan-demo-1/transport-fixture.bin', 'role': 'primary',
               'size_bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest(),
               'media_type': 'application/octet-stream', 'format_hint': 'transport-fixture-not-wsi'}],
}
save('acquisition-manifest.example.json', sample_manifest)
(ROOT / 'transport-fixture.bin').write_bytes(b'abc')

node_types = ['parse', 'normalize', 'resolve', 'validate', 'wait_dependencies', 'assets.ensure',
              'format.convert', 'package.freeze', 'target.map', 'target.validate',
              'deliver.push', 'deliver.publish', 'receipt.wait', 'reconcile', 'notify']
node = obj({'id': string(pattern='^[a-z][a-z0-9_]{0,63}$'), 'type': {'enum': node_types},
            'plugin_ref': SCHEMA_REF, 'parameters': {'type': 'object'},
            'timeout_seconds': POSITIVE,
            'retry': obj({'max_attempts': POSITIVE, 'initial_delay_seconds': POSITIVE,
                          'max_delay_seconds': POSITIVE}, ['max_attempts', 'initial_delay_seconds', 'max_delay_seconds'])},
           ['id', 'type', 'plugin_ref', 'parameters', 'timeout_seconds', 'retry'])
workflow = obj({'workflow_key': string(), 'revision': POSITIVE, 'input_schema': SCHEMA_REF,
                'trigger': {'enum': ['package_changed', 'manual', 'scheduled']},
                'nodes': arr(node, 1),
                'edges': arr(obj({'from': string(), 'to': string(), 'on': {'enum': ['success', 'invalid', 'needs_review']}},
                                 ['from', 'to', 'on'])),
                'production_enabled': {'type': 'boolean'}},
               ['workflow_key', 'revision', 'input_schema', 'trigger', 'nodes', 'edges', 'production_enabled'])
workflow.update({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': BASE + 'workflow.schema.json'})
save('workflow.schema.json', workflow)


def workflow_sample(key, domain, steps):
    return {'workflow_key': key, 'revision': 1, 'input_schema': {'key': domain, 'version': '1.0'},
            'trigger': 'package_changed', 'production_enabled': False,
            'nodes': [{'id': nid, 'type': kind, 'plugin_ref': {'key': plugin, 'version': '1.0'},
                       'parameters': params, 'timeout_seconds': 300,
                       'retry': {'max_attempts': 3, 'initial_delay_seconds': 5, 'max_delay_seconds': 300}}
                      for nid, kind, plugin, params in steps],
            'edges': [{'from': a[0], 'to': b[0], 'on': 'success'} for a, b in zip(steps, steps[1:])]}

save('pathology-workflow.example.json', workflow_sample('pathology.mock_push', 'pathology.candidate', [
    ('resolve', 'resolve', 'pathology.resolve', {}),
    ('ready', 'wait_dependencies', 'pathology.completeness', {'policy': 'whole_case'}),
    ('freeze', 'package.freeze', 'core.package', {}),
    ('map', 'target.map', 'profile.pathology_internal_v1', {}),
    ('validate', 'target.validate', 'core.schema', {}),
    ('send', 'deliver.push', 'destination.mock_http', {'destination_ref': 'mock-only'}),
    ('receipt', 'receipt.wait', 'destination.mock_http', {}),
]))
save('metrics-workflow.example.json', workflow_sample('metrics.mock_push', 'metrics.monthly', [
    ('validate', 'validate', 'core.schema', {}),
    ('freeze', 'package.freeze', 'core.package', {}),
    ('map', 'target.map', 'profile.metrics_mock', {}),
    ('send', 'deliver.push', 'destination.mock_http', {'destination_ref': 'mock-only'}),
]))

metrics = obj({'organization_code': string(), 'period': string(pattern='^[0-9]{4}-(0[1-9]|1[0-2])$'),
               'metric_code': string(), 'quantity': NONNEG,
               'amount': string(pattern='^-?[0-9]+\\.[0-9]{2}$')},
              ['organization_code', 'period', 'metric_code', 'quantity', 'amount'])
metrics.update({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': BASE + 'metrics.schema.json'})
save('metrics.schema.json', metrics)
save('metrics.example.json', {'organization_code': 'ORG-DEMO', 'period': '2026-09',
                             'metric_code': 'COUNT-DEMO', 'quantity': 12, 'amount': '123.45'})
(ROOT / 'metrics.example.csv').write_text('organization_code,period,metric_code,quantity,amount\nORG-DEMO,2026-09,COUNT-DEMO,12,123.45\n', encoding='utf-8-sig')

save('destination-profile.example.json', {
    'profile_key': 'jinan-imaging-v2-draft', 'revision': 1,
    'mode': 'PULL_PUBLICATION', 'production_enabled': False,
    'source_document': '济南市医保影像云数据采集标准规范V2.0（试行）',
    'approvals': {'status': 'UNCONFIRMED', 'blocking_issues': ['P01', 'P02', 'P03', 'P08', 'P09', 'P12', 'P14']},
    'capabilities': {'publish_database_views': True, 'publish_https_assets': True,
                     'business_receipts': 'UNCONFIRMED', 'accepts_vendor_wsi': 'UNCONFIRMED'},
    'dataset_names': {'examination': {'selected': None, 'candidates': ['CHK_INFO', 'CHECK_INFO']},
                      'item_dictionary': {'selected': None, 'candidates': ['APPLY_ITEM_INFO', 'APPLY_ITEM_DIC']}},
    'asset_delivery': {'url_policy': 'STABLE_AUTHENTICATED_ENDPOINT', 'authentication': 'TO_BE_CONFIRMED'},
    'unsupported_format_action': 'BLOCK', 'unknown_remote_result_action': 'WAIT_RECONCILIATION',
})
save('mapping.example.json', {
    'mapping_key': 'pathology.local_to_internal', 'revision': 1, 'production_enabled': False,
    'rules': [
        {'target': '/case/pathology_no', 'source': '/pis/pathologyNo', 'op': 'identity', 'on_missing': 'block'},
        {'target': '/index/certificate_type', 'source': '/his/certificateKind', 'op': 'dictionary_lookup',
         'dictionary_ref': {'namespace': 'hospital_demo_to_GA_cert', 'version': 'unapproved-draft'}, 'on_unmapped': 'block'},
        {'target': '/index/application_time', 'source': '/pis/requestTime', 'op': 'format_datetime',
         'input_timezone': 'Asia/Shanghai', 'output_format': 'YYYYMMDDHHmmss', 'on_missing': 'block'},
    ],
    'note': '字段和字典为模拟占位，未确认映射不得用于生产。输出格式令牌由映射引擎定义，不能未经转换直接传给strftime。',
})

# OpenAPI intentionally covers critical internal routes, not a complete implementation.
ref = lambda key: {'$ref': '#/components/schemas/' + key}
error = obj({'error': obj({'code': string(), 'message': string(), 'retryable': {'type': 'boolean'},
                          'field_errors': arr({'type': 'object'})}, ['code', 'message', 'retryable']),
             'request_id': string()}, ['error', 'request_id'])
schemas = {
    'Error': error,
    'Manifest': {k: v for k, v in manifest.items() if k not in ['$schema', '$id']},
    'AcquisitionResult': obj({'data': obj({'acquisition_id': UUID, 'assets': arr(obj({'client_asset_id': UUID, 'asset_id': UUID}, ['client_asset_id', 'asset_id']), 1)}, ['acquisition_id', 'assets']), 'request_id': string()}, ['data', 'request_id']),
    'UploadCreate': obj({'acquisition_id': UUID, 'asset_id': UUID, 'size_bytes': NONNEG, 'sha256': HASH}, ['acquisition_id', 'asset_id', 'size_bytes', 'sha256']),
    'UploadSession': obj({'data': obj({'session_id': UUID, 'asset_id': UUID, 'state': {'enum': ['CREATED', 'RECEIVING', 'VERIFYING', 'COMPLETED', 'FAILED', 'ABORTED', 'EXPIRED']}, 'chunk_size': POSITIVE, 'chunk_count': NONNEG, 'epoch': POSITIVE, 'expires_at': TIME, 'committed_chunks': arr(NONNEG), 'next_cursor': {'type': ['string', 'null']}}, ['session_id', 'asset_id', 'state', 'chunk_size', 'chunk_count', 'epoch', 'expires_at', 'committed_chunks', 'next_cursor']), 'request_id': string()}, ['data', 'request_id']),
    'AsyncResult': obj({'data': obj({'operation_id': UUID}, ['operation_id']), 'request_id': string()}, ['data', 'request_id']),
    'Operation': obj({'data': obj({'operation_id': UUID, 'state': {'enum': ['PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED']}, 'result': {'type': ['object', 'null']}, 'error_code': {'type': ['string', 'null']}}, ['operation_id', 'state', 'result', 'error_code']), 'request_id': string()}, ['data', 'request_id']),
    'PublicationCreate': obj({'package_revision_id': UUID, 'destination_id': UUID}, ['package_revision_id', 'destination_id']),
}


def response(description, schema=None):
    r = {'description': description}
    if schema:
        r['content'] = {'application/json': {'schema': ref(schema)}}
    return r


def pathparam(name, integer=False):
    return {'name': name, 'in': 'path', 'required': True, 'schema': NONNEG if integer else UUID}


def header(name, schema=None):
    return {'name': name, 'in': 'header', 'required': True, 'schema': schema or string()}


def operation(operation_id, success_code, success_schema=None, body=None, parameters=None, description=''):
    result = {'operationId': operation_id, 'description': description,
              'responses': {str(success_code): response('成功或异步受理；不代表医保业务确认', success_schema),
                            'default': response('结构化错误；依据code/retryable处理', 'Error')}}
    if body:
        result['requestBody'] = {'required': True, 'content': {'application/json': {'schema': ref(body)}}}
    if parameters:
        result['parameters'] = parameters
    return result

paths = {
    '/acquisitions': {'post': operation('registerAcquisition', 201, 'AcquisitionResult', 'Manifest', [header('Idempotency-Key')], 'tenant由认证推导；client_asset_id映射到服务端asset_id；重复请求返回同一资源。')},
    '/upload-sessions': {'post': operation('createUploadSession', 201, 'UploadSession', 'UploadCreate', [header('Idempotency-Key')])},
    '/upload-sessions/{session_id}': {
        'get': operation('getUploadSession', 200, 'UploadSession', parameters=[pathparam('session_id'), {'name': 'cursor', 'in': 'query', 'schema': string()}]),
        'delete': operation('abortUploadSession', 202, 'AsyncResult', parameters=[pathparam('session_id')]),
    },
    '/upload-sessions/{session_id}/chunks/{index}': {'put': operation('putUploadChunk', 204, parameters=[pathparam('session_id'), pathparam('index', True), header('X-Chunk-SHA256', HASH), header('X-Session-Epoch', POSITIVE)], description='固定分片索引决定偏移；同片同hash幂等，同片不同内容409；hash不符422；超大413；过期410；限流429。')},
    '/upload-sessions/{session_id}/complete': {'post': operation('completeUploadSession', 202, 'AsyncResult', parameters=[pathparam('session_id'), header('X-Session-Epoch', POSITIVE), header('Idempotency-Key')], description='异步整文件校验；缺片409；重复complete返回同一operation。')},
    '/operations/{operation_id}': {'get': operation('getOperation', 200, 'Operation', parameters=[pathparam('operation_id')])},
    '/assets/{asset_id}/content': {},
    '/publications': {'post': operation('createPublication', 202, 'AsyncResult', 'PublicationCreate', [header('Idempotency-Key')], '仅发布已验证快照；对方读取不是业务确认。')},
}
chunk_op = paths['/upload-sessions/{session_id}/chunks/{index}']['put']
chunk_op['requestBody'] = {'required': True, 'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}
for method in ['get', 'head']:
    paths['/assets/{asset_id}/content'][method] = {
        'operationId': method + 'AssetContent', 'parameters': [pathparam('asset_id'), {'name': 'Range', 'in': 'header', 'schema': string()}, {'name': 'If-Range', 'in': 'header', 'schema': string()}],
        'responses': {'200': {'description': '完整响应；HEAD无body'}, '206': {'description': '单段Range内容'}, '416': {'description': '范围不满足'}, 'default': response('授权或资源错误', 'Error')},
    }
spec = {'openapi': '3.1.0', 'info': {'title': '院内归集平台关键内部API设计草案', 'version': '0.3.0', 'description': '非医保/厂商协议；尚无服务实现。完整接口目录见04。'},
        'servers': [{'url': 'https://reporting.example.invalid/api/v1'}],
        'security': [{'bearerAuth': []}], 'paths': paths,
        'components': {'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}, 'schemas': schemas}}
(ROOT / 'internal-openapi.yaml').write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding='utf-8')
print('Generated draft schemas, examples, and OpenAPI in', ROOT)
