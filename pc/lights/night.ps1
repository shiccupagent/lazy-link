# Lazy Link night mode, started by the scheduled task "LazyLink Lights Off". Runs as lazylink; no admin.
param([string]$Until = '07:55')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = 'C:\Program Files\OpenRGB\OpenRGB.exe'
$py = Get-Content (Join-Path $here 'python-path.txt') -Raw
$log = Join-Path $here 'night.log'
"$(Get-Date -Format s) start" | Add-Content $log
$srv = Start-Process -FilePath $exe -ArgumentList '--server','--server-port','6742','--noautoconnect' -PassThru -WindowStyle Hidden
try {
    $up = $false
    for ($i = 0; $i -lt 30; $i++) { Start-Sleep 2; try { $t = New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1', 6742); $t.Close(); $up = $true; break } catch {} }
    if (-not $up) { "$(Get-Date -Format s) OpenRGB server did not start" | Add-Content $log; return }
    Start-Sleep 8
    $whl = Get-ChildItem $here -Filter '*.whl' | Select-Object -First 1 -ExpandProperty FullName
    & $py.Trim() (Join-Path $here 'lights.py') $whl $Until 2>&1 | Add-Content $log
} finally {
    if (-not $srv.HasExited) { Stop-Process -Id $srv.Id -Force }
    "$(Get-Date -Format s) end" | Add-Content $log
}
