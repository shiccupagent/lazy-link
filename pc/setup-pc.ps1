#Requires -RunAsAdministrator
<#
setup-pc.ps1 - one-time setup so your Mac can send AI jobs to this PC. Run it through
Setup-PC.cmd (double-click; Windows asks for admin permission once).

What it changes (undo-pc.ps1 reverses all of it):
  1. Installs and starts Windows' built-in OpenSSH Server.
  2. Creates a hidden, standard (non-admin) local account "render". It can only sign in
     with your Mac's key; its password is random, never shown, and kept only by Task Scheduler.
  3. sshd_config: key-only logins, and only "render" may log in (your old file is backed up).
  4. Firewall: port 22 open on Private networks, from your home network only.
  5. A work folder (C:\render by default) that "render" can write to.
  6. The job runner: a scheduled task that runs jobs as "render", even when nobody is signed in.
  7. Turns on Wake on Magic Packet on the wired network adapter, if it has the setting.
Then it shows the address, the network card's MAC address and the SSH fingerprint your Mac
needs, and saves them to <work folder>\pc-info.json (the Mac reads it when it pairs).

  Setup-PC.cmd                  do it (asks for the Mac's key if mac-key.pub is not beside it)
  Setup-PC.cmd -DryRun          only say what it would do
#>
param(
    [switch]$DryRun,
    [string]$WorkRoot = 'C:\render',
    [string]$MacKeyFile = (Join-Path $PSScriptRoot 'mac-key.pub')
)
$ErrorActionPreference = 'Stop'

$User = 'render'
$SshDir = Join-Path $env:ProgramData 'ssh'
$Sshd = Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'
$KeysFile = Join-Path $SshDir 'render_authorized_keys'
$Config = Join-Path $SshDir 'sshd_config'
$Backup = Join-Path $SshDir 'sshd_config.before-render-link'
$SidSystem = '*S-1-5-18'; $SidAdmins = '*S-1-5-32-544'; $SidUsers = 'S-1-5-32-545'

function Note([string]$m) { Write-Host $m }
function Step([string]$what, [scriptblock]$do) {
    if ($DryRun) { Note "[dry-run] would: $what" } else { Note "-> $what"; & $do }
}
function New-Password {
    $chars = [char[]]'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!#%+=?'
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    (-join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })) + 'aA1!'
}

Note "render-link setup$(if ($DryRun) {' (DRY RUN)'})"
# Files from a downloaded zip carry a "came from the internet" mark that can block them.
Get-ChildItem $PSScriptRoot -File | Unblock-File -ErrorAction SilentlyContinue

# 0. The Mac's public key ------------------------------------------------------------------
if (Test-Path $MacKeyFile) { $MacKey = (Get-Content $MacKeyFile -Raw).Trim() }
else {
    Note ''
    Note 'Paste the line your Mac printed (it starts with ssh-ed25519), then press Enter:'
    $MacKey = (Read-Host).Trim()
}
if ($MacKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]{60,}( .*)?$') {
    throw 'That does not look like a Mac public key (one line starting with ssh-ed25519). Run install-mac.sh on the Mac first.'
}

# 1. OpenSSH Server --------------------------------------------------------------------------
$cap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*' | Select-Object -First 1
if (-not $cap) { throw 'This Windows has no OpenSSH Server feature to install.' }
if ($cap.State -ne 'Installed') {
    Step 'install OpenSSH Server (a Windows feature, takes a minute)' { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
} else { Note 'OpenSSH Server already installed' }
if (-not $DryRun -and -not (Test-Path (Join-Path $SshDir 'ssh_host_ed25519_key.pub'))) {
    # The first start creates C:\ProgramData\ssh, the host keys and the default config.
    Step 'first start of sshd to create its host keys' { Start-Service sshd; Start-Sleep 2; Stop-Service sshd }
    if (-not (Test-Path (Join-Path $SshDir 'ssh_host_ed25519_key.pub'))) { throw 'sshd did not create its host keys.' }
}

# What the PC had before, so undo-pc.ps1 can put it back exactly.
$StateFile = Join-Path $SshDir 'render-link-state.json'
if (-not $DryRun -and -not (Test-Path $StateFile)) {
    $rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
    $svc = Get-Service sshd -ErrorAction SilentlyContinue
    @{ builtinRuleEnabled = [bool]($rule -and $rule.Enabled -eq 'True')
       sshdStartup = $(if ($svc) { [string]$svc.StartType } else { 'Manual' }) } |
        ConvertTo-Json | Set-Content -Path $StateFile -Encoding ascii
}

# 2. The "render" account ----------------------------------------------------------------------
# One random password, set here and handed only to Task Scheduler (step 6). Nobody sees it.
$pw = New-Password
if (-not (Get-LocalUser -Name $User -ErrorAction SilentlyContinue)) {
    Step "create hidden standard account '$User'" {
        New-LocalUser -Name $User -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires `
            -AccountNeverExpires -UserMayNotChangePassword -Description 'render-link jobs (SSH key only)' | Out-Null
        Add-LocalGroupMember -SID $SidUsers -Member $User
    }
} else {
    Step "account '$User' exists: give it a fresh random password" {
        Set-LocalUser -Name $User -Password (ConvertTo-SecureString $pw -AsPlainText -Force)
    }
}
if (-not $DryRun) {
    $admins = Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { ($_.Name -split '\\')[-1] }
    if ($admins -contains $User) { throw "'$User' is an administrator. Refusing: it must be a standard account." }
}
Step 'hide it from the sign-in screen' {
    $k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
    New-Item -Path $k -Force | Out-Null
    New-ItemProperty -Path $k -Name $User -Value 0 -PropertyType DWord -Force | Out-Null
}

# 3. Key file + sshd_config -------------------------------------------------------------------
Step "allow only the Mac's key for '$User'" {
    Set-Content -Path $KeysFile -Value $MacKey -Encoding ascii
    icacls $KeysFile /inheritance:r /grant "${SidSystem}:F" /grant "${SidAdmins}:F" /grant "${User}:R" | Out-Null
}
Step 'write sshd_config: key-only, only render may log in (old one backed up)' {
    if ((Test-Path $Config) -and -not (Test-Path $Backup)) { Copy-Item $Config $Backup }
    @(
        '# Written by render-link setup-pc.ps1. Original: sshd_config.before-render-link',
        'Port 22',
        'PubkeyAuthentication yes',
        'PasswordAuthentication no',
        'ChallengeResponseAuthentication no',
        'PermitEmptyPasswords no',
        'AllowUsers render',
        'AllowTcpForwarding no',
        'X11Forwarding no',
        'Subsystem sftp sftp-server.exe',
        '',
        'Match User render',
        '    AuthorizedKeysFile __PROGRAMDATA__/ssh/render_authorized_keys'
    ) | Set-Content -Path $Config -Encoding ascii
    & $Sshd -t
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path $Backup) { Copy-Item $Backup $Config -Force }
        throw 'The new sshd_config failed its check; the old one was put back.'
    }
}
Step 'make PowerShell the SSH shell' {
    New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -PropertyType String -Force `
        -Value (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') | Out-Null
}

# 4. Firewall ------------------------------------------------------------------------------------
Step 'firewall: port 22 on Private networks, from this home network only' {
    Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    Get-NetFirewallRule -Name 'RenderLink-SSH' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name 'RenderLink-SSH' -DisplayName 'render-link SSH (home network only)' `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Program $Sshd -Profile Private `
        -RemoteAddress LocalSubnet -Action Allow | Out-Null
}

# 5. Work folder + sshd service ---------------------------------------------------------------------
Step "work folder $WorkRoot (render may write there)" {
    foreach ($d in 'jobs', 'runner', 'tools', 'models', 'tmp') { New-Item -ItemType Directory -Path (Join-Path $WorkRoot $d) -Force | Out-Null }
    icacls $WorkRoot /grant "${User}:(OI)(CI)M" | Out-Null
}
Step 'start sshd now and at every boot' { Set-Service sshd -StartupType Automatic; Restart-Service sshd -Force }

# 6. The job runner -----------------------------------------------------------------------------------
# Jobs can't just run inside an SSH session: Windows' OpenSSH ends every process of a session
# when it closes, so a long render would die with the link. A scheduled task runs them instead.
$Runner = Join-Path $WorkRoot 'runner\runner.ps1'
Step 'copy the job runner' { Copy-Item (Join-Path $PSScriptRoot 'runner.ps1') $Runner -Force }
Step "grant render 'Log on as a batch job' (a scheduled task needs it)" {
    $sid = (Get-LocalUser -Name $User).SID.Value
    $sec = Join-Path $env:TEMP 'renderlink-sec'
    secedit /export /cfg "$sec.inf" /areas USER_RIGHTS | Out-Null
    $line = Get-Content "$sec.inf" | Where-Object { $_ -like 'SeBatchLogonRight*' }
    if (-not $line -or $line -notmatch [regex]::Escape($sid)) {
        $new = if ($line) { "$line,*$sid" } else { "SeBatchLogonRight = *$sid" }
        @('[Unicode]', 'Unicode=yes', '[Version]', 'signature="$CHICAGO$"', 'Revision=1', '[Privilege Rights]', $new) |
            Set-Content "$sec-new.inf" -Encoding Unicode
        secedit /configure /db "$sec.sdb" /cfg "$sec-new.inf" /areas USER_RIGHTS | Out-Null
    }
    Remove-Item "$sec*" -ErrorAction SilentlyContinue
}
Step 'register the scheduled task RenderLink\RenderRunner (runs as render, from boot)' {
    $ps = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $loop = "while (`$true) { & '$ps' -NoProfile -ExecutionPolicy Bypass -File '$Runner'; Start-Sleep 5 }"
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($loop))
    $action = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -WindowStyle Hidden -EncodedCommand $enc" -WorkingDirectory $WorkRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    # A password task, not S4U: S4U registration is refused on some PCs (0x80070005 even as admin).
    Register-ScheduledTask -TaskName RenderRunner -TaskPath '\RenderLink\' -Action $action -Trigger $trigger `
        -Settings $settings -User "$env:COMPUTERNAME\$User" -Password $pw -RunLevel Limited -Force | Out-Null
    Stop-ScheduledTask -TaskName RenderRunner -TaskPath '\RenderLink\' -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName RenderRunner -TaskPath '\RenderLink\'
}
$pw = $null

# 7. Wake-on-LAN ---------------------------------------------------------------------------------------
$nic = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
    Where-Object { $_.Status -eq 'Up' -and $_.MediaType -eq '802.3' } | Select-Object -First 1
if ($nic) {
    Step "Wake on Magic Packet on '$($nic.Name)'" {
        try { Set-NetAdapterPowerManagement -Name $nic.Name -WakeOnMagicPacket Enabled -ErrorAction Stop } catch { Note "  (power setting not available: $($_.Exception.Message))" }
        try { Set-NetAdapterAdvancedProperty -Name $nic.Name -DisplayName 'Wake on Magic Packet' -DisplayValue 'Enabled' -ErrorAction Stop } catch { Note '  (no Wake on Magic Packet driver setting)' }
    }
} else { Note 'No wired network adapter is connected: Wake-on-LAN skipped (it works over a cable only).' }

if ($DryRun) { Note ''; Note 'Dry run done: nothing was changed.'; return }

# Check + what the Mac needs -----------------------------------------------------------------------------
Note ''
$beat = Join-Path $WorkRoot 'runner.heartbeat'
$ok = $false
for ($i = 0; $i -lt 15 -and -not $ok; $i++) { Start-Sleep 2; $ok = (Test-Path $beat) -and ((Get-Date) - (Get-Item $beat).LastWriteTime).TotalSeconds -lt 30 }
if ($ok) { Write-Host 'Job runner: running' -ForegroundColor Green }
else { Write-Host "Job runner: NOT running yet (last result $((Get-ScheduledTaskInfo -TaskName RenderRunner -TaskPath '\RenderLink\').LastTaskResult)). See docs/TROUBLESHOOTING.md." -ForegroundColor Red }

$info = [ordered]@{ networks = @(); fingerprint = ''; gpu = ''; fast_startup = $false; disks = @() }
Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object {
    $info.networks += [ordered]@{ name = $_.InterfaceAlias; ip = $_.IPv4Address.IPAddress; prefix = $_.IPv4Address.PrefixLength
        mac = $_.NetAdapter.MacAddress; wired = ($_.NetAdapter.MediaType -eq '802.3'); category = [string]$_.NetProfile.NetworkCategory }
}
$hk = Join-Path $SshDir 'ssh_host_ed25519_key.pub'
$info.fingerprint = ((& (Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe') -lf $hk) -split ' ')[1]
try { $info.gpu = (& nvidia-smi '--query-gpu=name,memory.total' '--format=csv,noheader') -join '; ' } catch { $info.gpu = 'nvidia-smi not found' }
$info.fast_startup = ((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -ErrorAction SilentlyContinue).HiberbootEnabled -eq 1)
Get-PSDrive -PSProvider FileSystem | Where-Object Used -ne $null | ForEach-Object {
    $info.disks += [ordered]@{ drive = $_.Name; free_gb = [int]($_.Free / 1GB) }
}
$info | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $WorkRoot 'pc-info.json') -Encoding ascii

Write-Host ''
Write-Host '=== Tell your Mac (or just run the pair command below) ===' -ForegroundColor Cyan
foreach ($n in $info.networks) {
    Write-Host ("  {0}: address {1}   MAC {2}   {3}" -f $n.name, $n.ip, $n.mac, $(if ($n.wired) { 'wired' } else { 'Wi-Fi (Wake-on-LAN needs a cable)' }))
    if ($n.category -eq 'Public') {
        Write-Host "  WARNING: '$($n.name)' is set to Public, which blocks the Mac. Settings > Network > $($n.name) > Private network." -ForegroundColor Yellow
    }
}
Write-Host "  SSH fingerprint: $($info.fingerprint)"
if ($info.fast_startup) { Write-Host '  Fast Startup is on: Wake-on-LAN works from sleep, not from a full shutdown.' }
$ip = (@($info.networks | Where-Object { $_.wired }) + @($info.networks)) | Select-Object -First 1
if ($ip) { Write-Host ''; Write-Host "  On the Mac:  ./mac/pcjob pair $($ip.ip)" -ForegroundColor Cyan }
Write-Host '  It shows a fingerprint: it must match the one above.'
