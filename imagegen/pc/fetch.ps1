# job.ps1 for `imagegen.py fetch`: download the model files listed in want.json that the PC does not
# have yet. Official URL from models.json, resumable download to .part, size + sha256 checked before
# the file gets its real name. A file already in models\comfy or in one of your extra model folders
# (same name, same size) is skipped, so nothing is downloaded twice.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$m = Get-Content models.json -Raw | ConvertFrom-Json
$want = Get-Content want.json -Raw | ConvertFrom-Json
$extra = if (Test-Path link.json) { (Get-Content link.json -Raw | ConvertFrom-Json).extra_model_folders } else { $null }
$lib = Join-Path $env:RENDER_ROOT $m.library
$curl = "$env:SystemRoot\System32\curl.exe"
$report = @()
$need = 0
foreach ($name in $want) { $need += $m.files.$name.size }
$free = (Get-PSDrive ($lib.Substring(0, 1))).Free
"$($want.Count) file(s), $([math]::Round($need / 1GB, 1)) GB if none are there yet; $([math]::Round($free / 1GB)) GB free"
foreach ($name in $want) {
    $f = $m.files.$name
    if (-not $f) { throw "unknown file $name" }
    $dest = Join-Path $lib "$($f.dir)\$name"
    $have = @($dest) + @($(if ($extra -and $extra.($f.dir)) { $extra.($f.dir) | ForEach-Object { Join-Path $_ $name } }))
    $hit = $have | Where-Object { (Test-Path $_) -and (Get-Item $_).Length -eq $f.size } | Select-Object -First 1
    if ($hit) { "have  $name"; $report += [pscustomobject]@{ file = $name; state = 'had'; seconds = 0 }; continue }
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    $part = "$dest.part"
    "get   $name ($([math]::Round($f.size / 1GB, 2)) GB) from $($f.url)"
    $t = [Diagnostics.Stopwatch]::StartNew()
    $tries = 0
    while ($true) {
        $tries++
        & $curl -L --fail --silent --show-error --retry 5 --retry-delay 10 -C - -o $part $f.url
        if ($LASTEXITCODE -eq 0) { break }
        if ($tries -ge 4) { throw "download failed for $name (curl $LASTEXITCODE)" }
        "  curl exit $LASTEXITCODE, resuming (try $tries)"
        Start-Sleep 15
    }
    $len = (Get-Item $part).Length
    if ($len -ne $f.size) { throw "$name is $len bytes, expected $($f.size)" }
    $hash = (Get-FileHash $part -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $f.sha256) { Remove-Item $part; throw "$name sha256 mismatch ($hash)" }
    Move-Item $part $dest -Force
    $s = [int]$t.Elapsed.TotalSeconds
    "done  $name in $s s ($([math]::Round($f.size / 1MB / [math]::Max($s, 1), 1)) MB/s), sha256 ok"
    $report += [pscustomobject]@{ file = $name; state = 'downloaded'; seconds = $s }
}
$report | ConvertTo-Json | Set-Content (Join-Path $env:RENDER_OUT 'fetch.json')
