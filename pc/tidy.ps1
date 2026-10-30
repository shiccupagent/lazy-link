# tidy.ps1 - job for `./check.py --tidy`: how much room old files take on this PC, and (mode clean) the
# parts Lazy Link owns. Runs as the hidden 'lazylink' account (no admin), so it can't see other users'
# folders or Windows' own cleanup: those are reported as sizes, or as "not visible", and the owner cleans
# them. Nothing under Documents, Desktop, Downloads, Pictures, Videos, Music, another profile or a game
# folder is listed or touched. Writes $env:RENDER_OUT\tidy.json: labels and sizes only, never file names.
#
# want.json (written by check.py): {"mode": "scan"} (the default) or {"mode": "clean", "categories": [...]}.
# Clean mode deletes only the four Lazy Link categories below; any other id in the request is ignored.
$ErrorActionPreference = 'Continue'
$Root = $env:RENDER_ROOT
$Out = $env:RENDER_OUT
$Now = Get-Date
$OldDays = 14
$LazyIds = @('render_temp', 'lazy_jobs', 'lazy_tmp', 'lazy_logs')

$want = [ordered]@{ mode = 'scan'; categories = @() }
try {
    $w = Get-Content -LiteralPath 'want.json' -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($w.mode -eq 'clean') { $want.mode = 'clean' }
    if ($w.categories) { $want.categories = @($w.categories | ForEach-Object { [string]$_ }) }
} catch {}

$result = [ordered]@{ mode = $want.mode; checked = $Now.ToString('s'); categories = @() }

# Plain-English rows. action: 'lazy' = Lazy Link can clean it; 'user' = the owner cleans it (how = the step).
$Cats = @(
    @{ id = 'render_temp';  label = "This account's temp files (older than an hour)"; action = 'lazy'; how = '' },
    @{ id = 'lazy_jobs';    label = 'Old Lazy Link jobs (finished, 14+ days)';         action = 'lazy'; how = '' },
    @{ id = 'lazy_tmp';     label = 'Lazy Link temp folder';                           action = 'lazy'; how = '' },
    @{ id = 'lazy_logs';    label = 'Old Lazy Link log copies';                        action = 'lazy'; how = '' },
    @{ id = 'windows_temp'; label = 'Windows temp folder'; action = 'user';
       how = 'Settings > System > Storage > Temporary files' },
    @{ id = 'wu_downloads'; label = 'Old Windows Update downloads'; action = 'user';
       how = 'Settings > System > Storage > Temporary files: tick Windows Update Cleanup' },
    @{ id = 'nvidia';       label = 'NVIDIA installer leftovers'; action = 'user';
       how = 'delete C:\NVIDIA yourself once the driver works (the driver stays)' },
    @{ id = 'recycle_bin';  label = 'Recycle Bin (all drives)'; action = 'user';
       how = 'right-click the Recycle Bin on the desktop > Empty Recycle Bin' }
)

# Bytes under some paths that this account can read. readable = false: a folder it may not look inside.
# A path that doesn't exist counts as nothing (exists stays false).
function Get-Tree([string[]]$Paths) {
    $t = [ordered]@{ exists = $false; readable = $true; partial = $false; bytes = [long]0 }
    foreach ($p in $Paths) {
        if (-not $p -or -not (Test-Path -LiteralPath $p)) { continue }
        $t.exists = $true
        try { Get-ChildItem -LiteralPath $p -Force -ErrorAction Stop | Select-Object -First 1 | Out-Null }
        catch { $t.readable = $false; $t.partial = $true; continue }
        $errs = @()
        $sum = (Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue -ErrorVariable +errs |
            Measure-Object -Property Length -Sum).Sum
        $t.bytes += [long]$sum
        if ($errs.Count -gt 0) { $t.partial = $true }
    }
    return $t
}

# Finished Lazy Link jobs older than 14 days. Queued or running jobs (no status.json, or a live state) are never listed.
function Get-OldJobDirs {
    $jobs = Join-Path $Root 'jobs'
    $old = @()
    if (-not (Test-Path -LiteralPath $jobs)) { return ,$old }
    foreach ($d in @(Get-ChildItem -LiteralPath $jobs -Directory -Force -ErrorAction SilentlyContinue)) {
        $sf = Join-Path $d.FullName 'status.json'
        if (-not (Test-Path -LiteralPath $sf)) { continue }
        try { $st = (Get-Content -LiteralPath $sf -Raw | ConvertFrom-Json).state } catch { continue }
        if (($st -in @('done', 'failed', 'timeout', 'cancelled')) -and
            ((Get-Item -LiteralPath $sf).LastWriteTime -lt $Now.AddDays(-$OldDays))) {
            $old += $d.FullName
        }
    }
    return ,$old
}

# Rotated runner logs (runner.log.1 and so on). The live runner.log is never listed.
function Get-OldLogs {
    $logs = @()
    if ($Root -and (Test-Path -LiteralPath $Root)) {
        $logs = @(Get-ChildItem -LiteralPath $Root -Filter 'runner.log.*' -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
    return ,$logs
}

function Get-FixedRoots {
    return ,@([IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady } |
        ForEach-Object { $_.RootDirectory.FullName })
}

function Measure-Cat([string]$Id) {
    switch ($Id) {
        'render_temp'  { return Get-Tree @($env:TEMP) }
        'lazy_jobs'    { return Get-Tree @(Get-OldJobDirs) }
        'lazy_tmp'     { return Get-Tree @((Join-Path $Root 'tmp')) }
        'lazy_logs'    { return Get-Tree @(Get-OldLogs) }
        'windows_temp' { return Get-Tree @("$env:WINDIR\Temp") }
        'wu_downloads' { return Get-Tree @("$env:WINDIR\SoftwareDistribution\Download") }
        'nvidia'       { return Get-Tree @("$env:SystemDrive\NVIDIA", "$env:ProgramData\NVIDIA Corporation\Downloader") }
        'recycle_bin'  { return Get-Tree @(Get-FixedRoots | ForEach-Object { Join-Path $_ '$Recycle.Bin' }) }
    }
}

function Get-Entry($c) {
    $m = Measure-Cat $c.id
    return [ordered]@{ id = $c.id; label = $c.label; action = $c.action; how = $c.how
        exists = [bool]$m.exists; readable = [bool]$m.readable; partial = [bool]$m.partial; bytes = [long]$m.bytes }
}

# Only ever called with a Lazy Link id (checked by the caller). Each one removes what is inside, never the folder itself.
function Invoke-Clean([string]$Id) {
    switch ($Id) {
        'render_temp' {
            # an hour old at least: a program still writing its file keeps it
            Get-ChildItem -LiteralPath $env:TEMP -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $Now.AddHours(-1) } |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        'lazy_jobs' {
            foreach ($d in @(Get-OldJobDirs)) { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
        }
        'lazy_tmp' {
            Get-ChildItem -LiteralPath (Join-Path $Root 'tmp') -Force -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        'lazy_logs' {
            foreach ($f in @(Get-OldLogs)) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
        }
    }
}

try {
    if (-not $Root -or $Root -match '^[A-Za-z]:\\?$' -or -not $Out) {
        $result.error = 'The work folder is not set on this PC. Nothing was measured or deleted.'
    }
    elseif ($want.mode -eq 'clean') {
        $done = New-Object System.Collections.ArrayList
        foreach ($id in @($want.categories | Where-Object { $LazyIds -contains $_ } | Select-Object -Unique)) {
            $c = $Cats | Where-Object { $_.id -eq $id } | Select-Object -First 1
            $before = [long](Measure-Cat $id).bytes
            Invoke-Clean $id
            $e = Get-Entry $c
            $e.before = $before
            $e.freed = [long][math]::Max(0, $before - $e.bytes)
            [void]$done.Add($e)
        }
        $result.categories = $done
        $result.freed_bytes = [long](@($done | ForEach-Object { $_.freed }) | Measure-Object -Sum).Sum
    }
    else {
        $list = New-Object System.Collections.ArrayList
        foreach ($c in $Cats) { [void]$list.Add((Get-Entry $c)) }
        $result.categories = $list
    }
} catch {
    $result.error = 'The tidy-up stopped early.'
}

if ($Out) {
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Out 'tidy.json') -Encoding utf8
}
