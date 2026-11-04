#Requires -RunAsAdministrator
<#
setup-share.ps1 - a folder on this PC that your Mac opens like a drive (a Windows file share over your home
network). `mac/pcshare setup` copies it into the work folder and puts "Set up Mac shared folder" on the
Public Desktop; double-click that once and say Yes to the admin prompt. The shortcut deletes itself after.

What it changes (-Undo takes all of it off again, and keeps the files):
  1. A hidden, standard (non-admin) account "macshare" with a random password, used only by the Mac to open
     the share. The password goes to a file only the job account can read; `pcshare keychain` on the Mac
     moves it into the Mac's keychain and deletes the file. Nobody types or sees it.
  2. The folder (D:\MacShare by default). Its permissions start fresh (nothing inherited from the drive):
     you, Administrators, SYSTEM, "macshare" and the job account. Nobody else.
  3. The share "MacShare" for the same accounts, encrypted (SMB 3), showing each account only what it may open.
  4. Firewall: port 445 on Private networks, from your home network only. Windows' own "File and Printer
     Sharing" rules are left as they are.
  5. Makes sure the Windows file-sharing service (Server) is running.
Never touches sleep, power, restart, or the network's Private/Public setting: on a Public network it stops and
says so. It writes <work folder>\share\status.json for `pcshare check`.

  setup-share.ps1                    do it
  setup-share.ps1 -DryRun            only say what it would do
  setup-share.ps1 -Folder E:\Mac     another folder
  setup-share.ps1 -Undo              take the share, account and firewall rule off (the folder and files stay)
#>
param(
    [string]$Folder = 'D:\MacShare',
    [string]$Share = 'MacShare',
    [string]$Account = 'macshare',
    [string]$JobAccount = '',    # the Lazy Link job account; found from the work folder when empty
    [switch]$DryRun,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot                 # this file lives in <work folder>\share
$ShareDir = $PSScriptRoot
$Secret = Join-Path $ShareDir "$Account.secret"
$Status = Join-Path $ShareDir 'status.json'
$Rule = 'LazyLink-SMB'
$Shortcut = Join-Path $env:PUBLIC 'Desktop\Set up Mac shared folder.cmd'
$SidSystem = '*S-1-5-18'; $SidAdmins = '*S-1-5-32-544'; $SidUsers = 'S-1-5-32-545'

function Note([string]$m) { Write-Host $m }
function Step([string]$what, [scriptblock]$do) {
    if ($DryRun) { Note "[dry-run] would: $what" } else { Note "-> $what"; & $do }
}
function New-Password {
    $chars = [char[]]'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no symbols: it travels through a shell
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    (-join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })) + 'aA1'
}

if (-not $JobAccount) {
    $JobAccount = if ((Split-Path $Root -Leaf) -eq 'render') { 'render' } else { 'lazylink' }   # 'render' = before 2026-10-30
}
if (-not (Get-LocalUser -Name $JobAccount -ErrorAction SilentlyContinue)) { throw "No job account '$JobAccount' on this PC. Run Setup-PC first." }
$Me = [Security.Principal.WindowsIdentity]::GetCurrent().Name    # you (the account that said Yes to the admin prompt)

if ($Undo) {
    Step "remove the share '$Share' (the folder and its files stay)" { Get-SmbShare -Name $Share -ErrorAction SilentlyContinue | Remove-SmbShare -Force }
    Step "remove the firewall rule $Rule" { Get-NetFirewallRule -Name $Rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule }
    Step "remove the account '$Account'" {
        Remove-LocalUser -Name $Account -ErrorAction SilentlyContinue
        Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList' -Name $Account -ErrorAction SilentlyContinue
    }
    Step 'forget the status and password files' { Remove-Item $Secret, $Status -Force -ErrorAction SilentlyContinue }
    Note "Done. $Folder is still there with your files; delete it yourself if you want it gone."
    return
}

# 0. Checks: nothing changes unless all pass -----------------------------------------------------------
$drive = Split-Path -Qualifier $Folder
$vol = Get-Volume -DriveLetter $drive.TrimEnd(':') -ErrorAction SilentlyContinue
if (-not $vol) { throw "There is no drive $drive on this PC." }
if ($vol.FileSystem -ne 'NTFS') { throw "$drive is $($vol.FileSystem), not NTFS: Windows cannot set who may open the folder." }
$public = @(Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' -and $_.IPv4Connectivity -ne 'Disconnected' })
if ($public) {
    throw ("The network '$($public[0].Name)' is set to Public, and Windows blocks sharing there. That setting is yours: " +
           "Settings > Network & internet > Ethernet (or Wi-Fi) > Private network, then run this again.")
}
$other = Get-SmbShare -Name $Share -ErrorAction SilentlyContinue
if ($other -and ($other.Path.TrimEnd('\') -ne $Folder.TrimEnd('\'))) { throw "A share '$Share' already points at $($other.Path). Pick another -Share name, or -Undo first." }
Note ("{0}: {1:N0} GB free. The Mac will open it as \\{2}\{3}" -f $drive, ($vol.SizeRemaining / 1GB), $env:COMPUTERNAME, $Share)

# 1. The "macshare" account -----------------------------------------------------------------------------
$pw = New-Password
if (-not (Get-LocalUser -Name $Account -ErrorAction SilentlyContinue)) {
    Step "create hidden standard account '$Account' (only for the Mac to open the share)" {
        New-LocalUser -Name $Account -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires `
            -AccountNeverExpires -UserMayNotChangePassword -Description 'Lazy Link: the Mac opens the shared folder with it' | Out-Null
        Add-LocalGroupMember -SID $SidUsers -Member $Account
    }
} else {
    Step "account '$Account' exists: give it a fresh random password" {
        Set-LocalUser -Name $Account -Password (ConvertTo-SecureString $pw -AsPlainText -Force)
    }
}
if (-not $DryRun) {
    $admins = Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { ($_.Name -split '\\')[-1] }
    if ($admins -contains $Account) { throw "'$Account' is an administrator. Refusing: it must be a standard account." }
}
Step 'hide it from the sign-in screen' {
    $k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
    New-Item -Path $k -Force | Out-Null
    New-ItemProperty -Path $k -Name $Account -Value 0 -PropertyType DWord -Force | Out-Null
}

# 2. The folder -----------------------------------------------------------------------------------------
Step "folder ${Folder}: you, Administrators, SYSTEM, $Account and $JobAccount only" {
    New-Item -ItemType Directory -Path $Folder -Force | Out-Null
    # /inheritance:r drops what the drive passes down, including Setup-PC's DENY for the job account on other drives.
    icacls $Folder /inheritance:r /grant:r "${SidSystem}:(OI)(CI)F" "${SidAdmins}:(OI)(CI)F" "${Me}:(OI)(CI)F" `
        "${Account}:(OI)(CI)M" "${JobAccount}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls could not set the folder's permissions ($LASTEXITCODE)." }
}

# 3. The share ------------------------------------------------------------------------------------------
Step "share it as '$Share' (encrypted; each account sees only what it may open)" {
    if (Get-SmbShare -Name $Share -ErrorAction SilentlyContinue) { Remove-SmbShare -Name $Share -Force }
    New-SmbShare -Name $Share -Path $Folder -FullAccess $Me, $Account, $JobAccount -EncryptData $true `
        -FolderEnumerationMode AccessBased -CachingMode None -Description 'Lazy Link: your Mac opens this folder' | Out-Null
}

# 4. Firewall -------------------------------------------------------------------------------------------
Step 'firewall: port 445 on Private networks, from this home network only' {
    Get-NetFirewallRule -Name $Rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name $Rule -DisplayName 'Lazy Link shared folder (home network only)' `
        -Direction Inbound -Protocol TCP -LocalPort 445 -Program System -Profile Private `
        -RemoteAddress LocalSubnet -Action Allow | Out-Null
}

# 5. The file-sharing service ---------------------------------------------------------------------------
Step 'make sure the Server service (Windows file sharing) is running' {
    Set-Service -Name LanmanServer -StartupType Automatic
    Start-Service -Name LanmanServer
}

# 6. Hand-over to the Mac -------------------------------------------------------------------------------
Step 'leave the password for the Mac (only the job account can read it) and write status.json' {
    New-Item -ItemType Directory -Path $ShareDir -Force | Out-Null
    Set-Content -Path $Secret -Value $pw -Encoding ascii -NoNewline
    icacls $Secret /inheritance:r /grant:r "${SidSystem}:F" "${SidAdmins}:F" "${JobAccount}:M" | Out-Null
    $s = Get-SmbShare -Name $Share
    [ordered]@{
        share = $Share; unc = "\\$env:COMPUTERNAME\$Share"; folder = $Folder; computer = $env:COMPUTERNAME
        account = $Account; account_full = "$env:COMPUTERNAME\$Account"; job_account = $JobAccount
        encrypted = [bool]$s.EncryptData; firewall_rule = $Rule; free_gb = [math]::Round($vol.SizeRemaining / 1GB)
        set_up = (Get-Date).ToString('s')
    } | ConvertTo-Json | Set-Content -Path $Status -Encoding utf8
}
$pw = $null
Note ''
Note "Done. On the Mac: pcshare keychain, then pcshare check. The share is \\$env:COMPUTERNAME\$Share."
if (-not $DryRun -and (Test-Path $Shortcut)) { Note '(The desktop shortcut removes itself now.)' }
