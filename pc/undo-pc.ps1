#Requires -RunAsAdministrator
<#
undo-pc.ps1 - reverses setup-pc.ps1. Run it through Undo-PC.cmd (double-click).
  Stops SSH logins, puts the original sshd_config back, removes the firewall rule and the
  hidden "lazylink" account. It also removes anything left from before 2026-10-30, when the
  account was "render" and the folder C:\render. The work folder is kept unless
  -RemoveWorkFolder is given; OpenSSH Server stays installed (but stopped) unless
  -RemoveOpenSSH is given.
  Undo-PC.cmd                                    remove the link, keep C:\lazylink (tools + models)
  Undo-PC.cmd -RemoveWorkFolder -RemoveOpenSSH   remove everything
#>
param([switch]$RemoveWorkFolder, [switch]$RemoveOpenSSH, [string]$WorkRoot = 'C:\lazylink')
$ErrorActionPreference = 'Continue'
$SshDir = Join-Path $env:ProgramData 'ssh'
$Accounts = @('lazylink', 'render')          # render: the account before 2026-10-30
$Folders = @($WorkRoot, 'C:\render') | Select-Object -Unique
$SpecialKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'

# Before 2026-10-29 this project was called render-link: its names are undone too.
$state = $null
$stateFile = Join-Path $SshDir 'lazy-link-state.json'
if (-not (Test-Path $stateFile)) { $stateFile = Join-Path $SshDir 'render-link-state.json' }
if (Test-Path $stateFile) { $state = Get-Content $stateFile -Raw | ConvertFrom-Json }

foreach ($t in @(@('LazyRunner', '\LazyLink\'), @('RenderRunner', '\RenderLink\'))) {
    if (Get-ScheduledTask -TaskName $t[0] -TaskPath $t[1] -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $t[0] -TaskPath $t[1] -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $t[0] -TaskPath $t[1] -Confirm:$false
        Write-Host 'job runner task removed'
    }
}
# Take back 'Log on as a batch job' from both accounts (setup-pc.ps1 granted it to lazylink).
$sids = @(foreach ($a in $Accounts) { (Get-LocalUser -Name $a -ErrorAction SilentlyContinue).SID.Value } | Where-Object { $_ })
if ($sids.Count -gt 0) {
    $sec = Join-Path $env:TEMP 'lazylink-sec'
    secedit /export /cfg "$sec.inf" /areas USER_RIGHTS | Out-Null
    $line = Get-Content "$sec.inf" | Where-Object { $_ -like 'SeBatchLogonRight*' }
    if ($line -and ($sids | Where-Object { $line -match [regex]::Escape($_) })) {
        $rest = ($line -split '=', 2)[1].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -and ($sids -notcontains $_.TrimStart('*')) }
        @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]', ("SeBatchLogonRight = " + ($rest -join ','))) |
            Set-Content "$sec-new.inf" -Encoding Unicode
        secedit /configure /db "$sec.sdb" /cfg "$sec-new.inf" /areas USER_RIGHTS | Out-Null
        Write-Host "'Log on as a batch job' removed from the Lazy Link accounts"
    }
    Remove-Item "$sec*" -ErrorAction SilentlyContinue
}
Stop-Service sshd -ErrorAction SilentlyContinue
Set-Service sshd -StartupType $(if ($state) { $state.sshdStartup } else { 'Manual' }) -ErrorAction SilentlyContinue
if ($state -and $state.builtinRuleEnabled) { Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue }
$backup = Join-Path $SshDir 'sshd_config.before-lazy-link'
if (-not (Test-Path $backup)) { $backup = Join-Path $SshDir 'sshd_config.before-render-link' }
if (Test-Path $backup) { Move-Item $backup (Join-Path $SshDir 'sshd_config') -Force; Write-Host 'original sshd_config restored' }
Remove-Item (Join-Path $SshDir 'lazylink_authorized_keys') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $SshDir 'render_authorized_keys') -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -ErrorAction SilentlyContinue
Get-NetFirewallRule -Name 'LazyLink-SSH', 'RenderLink-SSH' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
foreach ($a in $Accounts) { Remove-ItemProperty -Path $SpecialKey -Name $a -ErrorAction SilentlyContinue }
foreach ($a in $Accounts) {
    $acct = Get-LocalUser -Name $a -ErrorAction SilentlyContinue
    if (-not $acct) { continue }
    $sid = $acct.SID.Value
    Remove-LocalUser -Name $a; Write-Host "account '$a' removed"
    Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object SID -eq $sid | Remove-CimInstance
    foreach ($f in $Folders) { if (Test-Path $f) { icacls $f /remove "*$sid" | Out-Null } }
}
Remove-Item $stateFile -ErrorAction SilentlyContinue
Write-Host 'Wake on Magic Packet is left on (harmless); turn it off in Device Manager if you want.'
if ($RemoveWorkFolder) {
    foreach ($f in $Folders) { if (Test-Path $f) { Remove-Item $f -Recurse -Force; Write-Host "$f removed" } }
}
if ($RemoveOpenSSH) {
    Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*' | Remove-WindowsCapability -Online | Out-Null
    Write-Host 'OpenSSH Server removed'
}
Write-Host 'Lazy Link removed.'
