# check.ps1 - job for `./check.py`: what this PC has, and how ready it is after months or years switched off.
# Runs as the hidden account (no admin, no WMI: that account can't use it), only reads, never changes or deletes
# anything. Writes $env:RENDER_OUT\check.json. No addresses, network-card IDs or user names go in it.
$ErrorActionPreference = 'Continue'
$r = [ordered]@{ checked = (Get-Date).ToString('s') }
function Try-Get([scriptblock]$b) { try { & $b } catch { $null } }

$cv = Try-Get { Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' }
$build = [int]$cv.CurrentBuildNumber
$r.os = [ordered]@{
    # Windows 11 still says "Windows 10" in ProductName; the build number tells them apart.
    name = $(if ($build -ge 22000) { $cv.ProductName -replace 'Windows 10', 'Windows 11' } else { $cv.ProductName })
    version = $cv.DisplayVersion; build = $build }
$r.uptime_hours = [math]::Round([Environment]::TickCount64 / 3600000, 1)
$r.cpu = [ordered]@{ name = (Try-Get { (Get-ItemProperty 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0').ProcessorNameString.Trim() })
                     threads = [Environment]::ProcessorCount }
Add-Type -AssemblyName Microsoft.VisualBasic
$r.ram_gb = Try-Get { [math]::Round((New-Object Microsoft.VisualBasic.Devices.ComputerInfo).TotalPhysicalMemory / 1GB, 1) }

# NVIDIA cards: nvidia-smi ships with the driver.
$smi = Join-Path $env:WINDIR 'System32\nvidia-smi.exe'
$r.gpus = @()
if (Test-Path $smi) {
    foreach ($line in (& $smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null)) {
        $f = $line -split ',\s*'
        if ($f.Count -ge 3) { $r.gpus += [ordered]@{ name = $f[0]; vram_gb = [math]::Round([double]$f[1] / 1024, 1); driver = $f[2] } }
    }
}

# Disks: free space per fixed drive (models need 10-80 GB).
$r.disks = @([IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady } | ForEach-Object {
    [ordered]@{ drive = $_.Name.TrimEnd('\'); total_gb = [math]::Round($_.TotalSize / 1GB); free_gb = [math]::Round($_.AvailableFreeSpace / 1GB) } })

# Last Windows update that installed (the update service answers standard users too).
$r.last_update = Try-Get {
    $s = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $n = $s.GetTotalHistoryCount()
    if ($n -gt 0) { ($s.QueryHistory(0, [math]::Min($n, 50)) | Where-Object { $_.ResultCode -eq 2 } |
        Sort-Object Date -Descending | Select-Object -First 1).Date.ToString('yyyy-MM-dd') } }

# Waking from the network: Fast Startup must be off, the wired card's "Wake on Magic Packet" on.
$r.fast_startup = Try-Get { [bool](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power').HiberbootEnabled }
$nets = [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
    Where-Object { $_.NetworkInterfaceType -in 'Ethernet', 'Wireless80211' -and $_.Description -notmatch 'Virtual|Hyper-V|VPN|Bluetooth|Loopback|TAP' }
$r.network = @($nets | ForEach-Object { [ordered]@{ kind = $(if ($_.NetworkInterfaceType -eq 'Ethernet') { 'cable' } else { 'wifi' })
    connected = ($_.OperationalStatus -eq 'Up'); mbps = $(if ($_.OperationalStatus -eq 'Up') { [int]($_.Speed / 1e6) } else { 0 }) } })
$cls = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
$r.wake_on_magic_packet = Try-Get {
    $vals = @(Get-ChildItem $cls -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue } |
        Where-Object { $_.'*WakeOnMagicPacket' -ne $null -and $_.DriverDesc -notmatch 'Wi-?Fi|Wireless|802\.11' } | ForEach-Object { $_.'*WakeOnMagicPacket' })
    if ($vals.Count) { [bool]($vals -contains '1') } else { $null } }

# Sleep timer when plugged in (minutes; 0 = never sleeps).
$r.sleep_after_min = Try-Get {
    $q = powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null | Select-String 'Current AC Power Setting Index'
    if ($q) { [int]([Convert]::ToInt64(($q.Line -split ':\s*')[-1].Trim(), 16) / 60) } }

# Extra model folders (config.json extra_model_folders, sent with every job as link.json): can the job account
# reach them, are they locked read-only for it (a DENY from Setup-PC), and how many model files are in them.
# Only the drive letter is reported, never the folder path (it can hold a user name).
$r.extra_folders = @()
$lk = Join-Path (Get-Location) 'link.json'
$extra = if (Test-Path $lk) { Try-Get { (Get-Content $lk -Raw | ConvertFrom-Json).extra_model_folders } }
foreach ($kind in @($extra.PSObject.Properties | Where-Object { $_.Name -notlike '_*' })) {
    foreach ($p in @($kind.Value | Where-Object { $_ -is [string] -and $_ })) {
        $item = [ordered]@{ kind = $kind.Name; drive = (Split-Path -Qualifier $p); exists = $false; readable = $false; model_files = 0; locked = $null }
        $item.exists = [bool](Try-Get { Test-Path -LiteralPath $p -PathType Container })
        if ($item.exists) {
            # Lazy enumeration, stopped at 500 files: a big model folder is not read in full.
            $item.readable = [bool](Try-Get {
                $n = 0
                foreach ($f in [IO.Directory]::EnumerateFiles($p, '*', [IO.SearchOption]::AllDirectories)) { $n++; if ($n -ge 500) { break } }
                $item.model_files = $n; $true })
            if ($item.readable) {
                # icacls only reads the ACL; a DENY entry for lazylink is what the lock looks like.
                $acl = (icacls $p 2>$null | Out-String)
                $item.locked = [bool]($acl -match '\\lazylink:[^\r\n]*\(DENY\)')
            }
        }
        $r.extra_folders += $item
    }
}

$r | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $env:RENDER_OUT 'check.json') -Encoding utf8
Get-Content (Join-Path $env:RENDER_OUT 'check.json')
