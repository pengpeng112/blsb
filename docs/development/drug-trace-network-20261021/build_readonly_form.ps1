# Fixed query only. Stdout is captured by dmz_query_probe.py, never a terminal/log.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
Add-Type -Path (Join-Path $root 'docs/development/platform-live-test-20261020/OfficialTopReference.cs')
Add-Type -AssemblyName System.Net.Http
if (-not $env:BLYB_ALIHEALTH_APP_SECRET) { throw 'Use the existing private credential wrapper.' }
$now = [DateTime]::UtcNow.AddHours(8)
$form = [Collections.Generic.Dictionary[string,string]]::new()
$form['method'] = 'alibaba.alihealth.drugtrace.top.yljg.query.billstatus'
$form['app_key'] = $env:BLYB_ALIHEALTH_APP_KEY
$form['ref_ent_id'] = $env:BLYB_ALIHEALTH_REF_ENT_ID
$form['timestamp'] = $now.ToString('yyyy-MM-dd HH:mm:ss')
$form['format'] = 'json'
$form['v'] = '2.0'
$form['sign_method'] = 'md5'
$form['begin_date'] = $now.ToString('yyyy-MM-dd')
$form['end_date'] = $now.ToString('yyyy-MM-dd')
$form['bill_type'] = 'A'
$form['bill_code'] = 'BLYB_READONLY_' + [Guid]::NewGuid().ToString('N')
$form['page_size'] = '1'
$form['page'] = '1'
$form['sign'] = [OfficialTopReference]::SignTopRequest($form, $env:BLYB_ALIHEALTH_APP_SECRET, 'md5')
$content = [Net.Http.FormUrlEncodedContent]::new($form)
try { [Console]::Out.Write($content.ReadAsStringAsync().GetAwaiter().GetResult()) }
finally { $content.Dispose(); $form.Clear() }
