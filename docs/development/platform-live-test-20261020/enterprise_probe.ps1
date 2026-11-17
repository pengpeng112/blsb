# One real, read-only TOP query using the official C# reference implementation.
# Not a replacement for the application's unverified Python signing path.
# No uploads, no retries, no patient data, no secret/signature output.
$ErrorActionPreference = 'Stop'
$publicPath = Join-Path $PSScriptRoot 'evidence/enterprise-probe-receipt.json'
if (Test-Path -LiteralPath $publicPath) { throw 'Receipt already exists; create a new revision instead of retrying.' }
Add-Type -Path (Join-Path $PSScriptRoot 'OfficialTopReference.cs')
Add-Type -AssemblyName System.Net.Http
$privateEvidence = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '../../..')) '.ai/private/alihealth-live-test-20261020'
if (-not (Test-Path -LiteralPath $privateEvidence -PathType Container)) { throw 'Protected evidence directory required.' }
if (-not $env:BLYB_ALIHEALTH_APP_KEY -or -not $env:BLYB_ALIHEALTH_APP_SECRET -or -not $env:BLYB_ALIHEALTH_REF_ENT_ID) {
    throw 'Use the private credential wrapper; never pass credentials as arguments.'
}
$nowUtc = [DateTime]::UtcNow
$chinaNow = $nowUtc.AddHours(8)
# Query only the caller hospital; no unrelated enterprise lookup.
$form = [System.Collections.Generic.Dictionary[string,string]]::new()
$form['method'] = 'alibaba.alihealth.drugtrace.top.yljg.query.getbyrefentid'
$form['app_key'] = $env:BLYB_ALIHEALTH_APP_KEY
$form['ref_ent_id'] = $env:BLYB_ALIHEALTH_REF_ENT_ID
$form['timestamp'] = $chinaNow.ToString('yyyy-MM-dd HH:mm:ss')
$form['format'] = 'json'
$form['v'] = '2.0'
$form['sign_method'] = 'md5'
$form['dest_ref_ent_id'] = $env:BLYB_ALIHEALTH_REF_ENT_ID
$form['sign'] = [OfficialTopReference]::SignTopRequest($form, $env:BLYB_ALIHEALTH_APP_SECRET, 'md5')
$receipt = [ordered]@{
    captured_at = $nowUtc.ToString('o')
    endpoint = 'https://eco.taobao.com/router/rest'
    route = 'Windows_to_official_HTTPS_gateway'
    method = $form['method']
    signer = 'official_CSharp_reference_article_107727'
    request_count = 0
    real_uploads = 0
    patient_data_sent = $false
    tls_validation_enabled = $true
    redirects_allowed = $false
    retries = 0
    app_python_signing_gate_changed = $false
    credentials_or_signatures_in_receipt = $false
    status = 'NOT_SENT'
}
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(20)
$content = [System.Net.Http.FormUrlEncodedContent]::new($form)
# Reserve outside the request catch: a concurrent reservation failure must not
# overwrite another process's receipt. Interruption leaves an uncertain outcome.
$receipt.status = 'RESERVED_OUTCOME_UNCONFIRMED'
$reservation = [IO.File]::Open($publicPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $pending = [Text.Encoding]::UTF8.GetBytes(($receipt | ConvertTo-Json -Depth 5))
    $reservation.Write($pending, 0, $pending.Length)
} finally { $reservation.Dispose() }
try {
    $receipt.request_count = 1
    $receipt.status = 'REQUEST_STARTED'
    $response = $client.PostAsync($receipt.endpoint, $content).GetAwaiter().GetResult()
    $receipt['http_status'] = [int]$response.StatusCode
    $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $responseName = 'enterprise-probe-' + $nowUtc.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N') + '.dpapi'
    $secureResponse = ConvertTo-SecureString $raw -AsPlainText -Force
    try {
        [IO.File]::WriteAllText((Join-Path $privateEvidence $responseName),
            (ConvertFrom-SecureString $secureResponse), [Text.UTF8Encoding]::new($false))
    } finally { $secureResponse.Dispose() }
    $receipt['encrypted_raw_response'] = '.ai/private/alihealth-live-test-20261020/' + $responseName
    try { $parsed = $raw | ConvertFrom-Json } catch { $parsed = $null }
    if ($null -eq $parsed) {
        $receipt.status = 'NON_JSON_RESPONSE'
    } elseif ($null -ne $parsed.error_response) {
        $err = $parsed.error_response
        $receipt.status = 'TOP_ERROR_RESPONSE'
        $receipt['error_code'] = [string]$err.code
        $receipt['sub_code'] = [string]$err.sub_code
        foreach ($field in @('msg','sub_msg')) {
            $message = [string]$err.$field
            foreach ($sensitive in @($env:BLYB_ALIHEALTH_APP_SECRET,$form['sign'],$env:BLYB_ALIHEALTH_REF_ENT_ID)) {
                if ($sensitive) { $message = $message.Replace($sensitive, '[REDACTED]') }
            }
            $receipt[$field] = $message.Substring(0,[Math]::Min(300,$message.Length))
        }
        $receipt['request_id'] = [string]$err.request_id
    } else {
        $body = $parsed.alibaba_alihealth_drugtrace_top_yljg_query_getbyrefentid_response
        $result = $body.result
        $receipt.status = 'BUSINESS_RESPONSE'
        $receipt['response_success'] = $result.response_success
        $receipt['msg_code'] = [string]$result.msg_code
        # Do not print or publish returned bill rows, even if a filter is ignored.
        $receipt['upload_permission_verified'] = $false
        $model = $result.model
        $receipt['returned_hospital_name'] = [string]$model.ent_name
        $receipt['ref_ent_id_matches_supplied'] = ([string]$model.ref_ent_id -eq $env:BLYB_ALIHEALTH_REF_ENT_ID)
        $receipt['hospital_name_matches_supplied'] = ([string]$model.ent_name -eq $env:BLYB_ALIHEALTH_HOSPITAL_NAME)
        $receipt['ent_id_present'] = -not [string]::IsNullOrEmpty([string]$model.ent_id)
        $receipt['enterprise_status_raw'] = [string]$model.status
        $receipt['enterprise_role_raw'] = [string]$model.user_role_type
        # A directory lookup does not prove ownership/binding of the application.
        $receipt['hospital_identity_binding_verified'] = $false
    }
    $response.Dispose()
} catch {
    $receipt.status = 'TRANSPORT_OR_LOCAL_ERROR'
    $receipt['error_type'] = $_.Exception.GetType().Name
    # Raw exception/request details are intentionally not logged.
} finally {
    $client.Dispose(); $handler.Dispose(); $content.Dispose()
    $form.Clear()
}
$json = $receipt | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($publicPath,$json,[Text.UTF8Encoding]::new($false))
$json
