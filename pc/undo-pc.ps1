#Requires -RunAsAdministrator
<#
undo-pc.ps1 - reverses setup-pc.ps1. Run it through Undo-PC.cmd (double-click).
  Stops SSH logins, puts the original sshd_config back, removes the firewall rule and the
  hidden "render" account. The work folder is kept unless -RemoveWorkFolder is given;
  OpenSSH Server stays installed (but stopped) unless -RemoveOpenSSH is given.
  Undo-PC.cmd                                  remove the link, keep C:\render (tools + models)
  Undo-PC.cmd -RemoveWorkFolder -RemoveOpenSSH  remove everything
#>
param([switch]$RemoveWorkFolder, [switch]$RemoveOpenSSH, [string]$WorkRoot = 'C:\render')
$ErrorActionPreference = 'Continue'
$SshDir = Join-Path $env:ProgramData 'ssh'

$state = $null
$stateFile = Join-Path $SshDir 'render-link-state.json'
if (Test-Path $stateFile) { $state = Get-Content $stateFile -Raw | ConvertFrom-Json }
if (Get-ScheduledTask -TaskName RenderRunner -TaskPath '\RenderLink\' -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName RenderRunner -TaskPath '\RenderLink\' -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName RenderRunner -TaskPath '\RenderLink\' -Confirm:$false
    Write-Host 'job runner task removed'
}
$acctSid = (Get-LocalUser -Name render -ErrorAction SilentlyContinue).SID.Value
if ($acctSid) {   # take back 'Log on as a batch job' (setup-pc.ps1 granted it)
    $sec = Join-Path $env:TEMP 'renderlink-sec'
    secedit /export /cfg "$sec.inf" /areas USER_RIGHTS | Out-Null
    $line = Get-Content "$sec.inf" | Where-Object { $_ -like 'SeBatchLogonRight*' }
    if ($line -and $line -match [regex]::Escape($acctSid)) {
        $rest = ($line -split '=', 2)[1].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne "*$acctSid" }
        @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]', ("SeBatchLogonRight = " + ($rest -join ','))) |
            Set-Content "$sec-new.inf" -Encoding Unicode
        secedit /configure /db "$sec.sdb" /cfg "$sec-new.inf" /areas USER_RIGHTS | Out-Null
        Write-Host "'Log on as a batch job' removed from render"
    }
    Remove-Item "$sec*" -ErrorAction SilentlyContinue
}
Stop-Service sshd -ErrorAction SilentlyContinue
Set-Service sshd -StartupType $(if ($state) { $state.sshdStartup } else { 'Manual' }) -ErrorAction SilentlyContinue
if ($state -and $state.builtinRuleEnabled) { Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue }
$backup = Join-Path $SshDir 'sshd_config.before-render-link'
if (Test-Path $backup) { Move-Item $backup (Join-Path $SshDir 'sshd_config') -Force; Write-Host 'original sshd_config restored' }
Remove-Item (Join-Path $SshDir 'render_authorized_keys') -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -ErrorAction SilentlyContinue
Get-NetFirewallRule -Name 'RenderLink-SSH' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList' -Name render -ErrorAction SilentlyContinue
$acct = Get-LocalUser -Name render -ErrorAction SilentlyContinue
if ($acct) {
    $sid = $acct.SID.Value
    Remove-LocalUser -Name render; Write-Host "account 'render' removed"
    Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object SID -eq $sid | Remove-CimInstance
    if (Test-Path $WorkRoot) { icacls $WorkRoot /remove "*$sid" | Out-Null }
}
Remove-Item $stateFile -ErrorAction SilentlyContinue
Write-Host 'Wake on Magic Packet is left on (harmless); turn it off in Device Manager if you want.'
if ($RemoveWorkFolder -and (Test-Path $WorkRoot)) { Remove-Item $WorkRoot -Recurse -Force; Write-Host "$WorkRoot removed" }
if ($RemoveOpenSSH) {
    Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*' | Remove-WindowsCapability -Online | Out-Null
    Write-Host 'OpenSSH Server removed'
}
Write-Host 'Render link removed.'
