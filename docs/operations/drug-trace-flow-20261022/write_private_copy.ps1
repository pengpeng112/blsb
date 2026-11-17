# User-requested local credential document. No credential values in this script.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$privateDir = Join-Path $root '.ai/private/drug-trace-flow-20261022'
[void][IO.Directory]::CreateDirectory($privateDir)

# Restrict the new directory before writing any plaintext requested by the user.
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($userSid)
$acl.SetAccessRuleProtection($true, $false)
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagate = [Security.AccessControl.PropagationFlags]::None
foreach ($sid in @($userSid, $systemSid)) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $sid, [Security.AccessControl.FileSystemRights]::FullControl,
        $inherit, $propagate, [Security.AccessControl.AccessControlType]::Allow)
    [void]$acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $privateDir -AclObject $acl

$connections = Import-Clixml -LiteralPath (Join-Path $root '.ai/private/reporting-20261015/connections.clixml')
$dmz = Import-Clixml -LiteralPath (Join-Path $root '.ai/private/reporting-20261015/dmz-credential.clixml')
$reader = Import-Clixml -LiteralPath (Join-Path $root '.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml')
$platformDir = Join-Path $root '.ai/private/alihealth-drugtrace-20261020'
$profile = Get-Content -LiteralPath (Join-Path $platformDir 'profile.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$pointer = [IntPtr]::Zero
$secret = $null

function Cell([string]$value) {
    return $value.Replace('|', '\|').Replace("`r", '').Replace("`n", '\n')
}

try {
    $secure = ConvertTo-SecureString (Get-Content -LiteralPath (Join-Path $platformDir 'app-secret.dpapi') -Raw -Encoding UTF8)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $targetPassword = $connections.Target.Credential.GetNetworkCredential().Password
    $jumpPassword = $connections.Jump.Credential.GetNetworkCredential().Password
    $dmzPassword = $dmz.GetNetworkCredential().Password
    $readerPassword = $reader.GetNetworkCredential().Password
    if ([string]::IsNullOrWhiteSpace($secret) -or [string]::IsNullOrWhiteSpace($readerPassword)) {
        throw 'Required local credential could not be resolved; no values printed.'
    }
    $rows = @(
        '| 用途 | 地址 | 用户名/标识 | 密码/密钥 |',
        '|---|---|---|---|',
        ('| 内网抽取/签名服务器SSH | <prod-host>:22 | ' + (Cell $connections.Target.Credential.UserName) + ' | ' + (Cell $targetPassword) + ' |'),
        ('| SSH运维跳板 | <frontend-host>4:<ssh-port> | ' + (Cell $connections.Jump.Credential.UserName) + ' | ' + (Cell $jumpPassword) + ' |'),
        ('| DMZ前置机SSH | <relay-net-host>:22 | ' + (Cell $dmz.UserName) + ' | ' + (Cell $dmzPassword) + ' |'),
        ('| HIS专用只读账号 | <his-host>:1521/his | ' + (Cell $reader.UserName) + ' | ' + (Cell $readerPassword) + ' |'),
        '| 数据资产辅助入口SSH | <frontend-host>3:22 | root | 使用现有C:UsersAdministrator.sshid_ed25519_ai，不抄录私钥内容 |',
        ('| 码上放心应用 | 官方TOP接口 | AppKey：' + (Cell $profile.app_key) + ' | AppSecret：' + (Cell $secret) + ' |'),
        ('| 医院上传身份 | ref_user_id取ref_ent_id | ref_ent_id：' + (Cell $profile.ref_ent_id) + ' | 非口令，机构标识 |')
    )
    $intro = @(
        '# 追溯码推送流程与账号密码（仅本机）',
        '',
        '应用户要求，汇总完整本机凭据。保存日期：2026-10-22；仅当前Windows账号和SYSTEM可访问，文件不进入Git或发行包。',
        '来源为既有用户提供的本机DPAPI记录；未读取<relay-jump>服务器托管数据库凭据、未打印密钥、未新增远程连接。',
        '',
        '## 当前使用的账号密码',
        '',
        ($rows -join "`r`n"),
        '',
        '密码大小写以表中原值为准。root用于SSH，不用于网页或HIS数据库。',
        '网页管理令牌保存在<prod-host>的/etc/blyb/runtime.env之BLYB_BOOTSTRAP_ADMIN_TOKEN，本次不抄录该令牌。',
        'BLYB_DRUGTRACE只有两个PHARMACY视图SELECT权限；当前Oracle服务名为his，不是hisuser。',
        'AppSecret仅供<prod-host>的worker签名，不放到DMZ或人工上报核查文件中。',
        '',
        '## 完整链路和以后人工记录核对方法',
        ''
    ) -join "`r`n"
    $guide = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Raw -Encoding UTF8
    # The private copy lives in another directory; preserve clickable targets.
    $guide = [regex]::Replace($guide, '\]\(([^)]+)\)', {
        param($match)
        $target = $match.Groups[1].Value
        if ($target -match '^(?:[A-Za-z][A-Za-z0-9+.-]*:|#)') { return $match.Value }
        $absolute = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $target)).Replace('\', '/')
        return '](' + $absolute + ')'
    })
    $output = Join-Path $privateDir '推送流程及账号密码_仅本机.md'
    if (Test-Path -LiteralPath $output) { throw 'Private output already exists; preserve its revision instead of overwriting.' }
    [IO.File]::WriteAllText($output, $intro + $guide, [Text.UTF8Encoding]::new($true))

    # Output presence and ACL checks only, never values, lengths or file content.
    $principals = @((Get-Acl -LiteralPath $privateDir).Access | ForEach-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    })
    $expected = @($userSid.Value, $systemSid.Value)
    $check = [ordered]@{
        private_document_written = (Test-Path -LiteralPath $output)
        credentials_complete = ($targetPassword.Length -gt 0 -and $jumpPassword.Length -gt 0 -and $dmzPassword.Length -gt 0 -and $readerPassword.Length -gt 0 -and $secret.Length -gt 0)
        acl_user_and_system_only = (@($principals | Where-Object { $_ -notin $expected }).Count -eq 0 -and $principals.Count -eq 2)
        remote_connections = 0
        credential_values_printed = $false
    }
    $check | ConvertTo-Json -Compress
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ($null -ne $secure) { $secure.Dispose() }
    $secret = $null; $targetPassword = $null; $jumpPassword = $null
    $dmzPassword = $null; $readerPassword = $null
}
