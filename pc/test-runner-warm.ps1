<#
test-runner-warm.ps1 - checks runner.ps1's warm-server code (start, reuse, restart, stop) on the PC, with a small
Python web server in place of ComfyUI: no GPU, no job queue, nothing of the real runner touched.
The functions are taken from runner.ps1 itself (its syntax tree), so this tests the code the runner runs.
  powershell -NoProfile -ExecutionPolicy Bypass -File test-runner-warm.ps1 -Python C:\path\python.exe [-Runner runner.ps1]
Exit 0 = every check passed.
#>
param([Parameter(Mandatory)][string]$Python, [string]$Runner, [int]$Port = 8199)
$ErrorActionPreference = 'Stop'
if (-not $Runner) { $Runner = Join-Path (Split-Path $MyInvocation.MyCommand.Path) 'runner.ps1' }   # PS 5.1: no $PSScriptRoot in param()
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Runner, [ref]$t, [ref]$e)
if ($e) { throw "runner.ps1 does not parse: $($e[0].Message)" }
$addType = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Add-Type' }, $true)
Invoke-Expression $addType.Extent.Text
foreach ($f in 'Stop-Warm', 'Test-WarmAnswers', 'Expand-Root', 'Resolve-Root', 'Use-Warm', 'Watch-Warm') {
    $fa = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $f }, $true)
    if (-not $fa) { throw "runner.ps1 has no function $f" }
    Invoke-Expression $fa.Extent.Text
}
$Root = Join-Path $env:TEMP ("warmtest-" + [guid]::NewGuid().ToString('n').Substring(0, 8))
$WarmDir = Join-Path $Root 'warm'; $Log = Join-Path $Root 'runner.log'
$script:Warm = $null
New-Item -ItemType Directory -Force $Root | Out-Null
function Write-Log($msg) { Add-Content $Log ("{0} {1}" -f (Get-Date -Format s), $msg) }
$fails = 0
function Check($what, $ok) { if ($ok) { "PASS $what" } else { "FAIL $what"; $script:fails++ } }
function Work($name, $spec) {
    $d = Join-Path $Root $name; New-Item -ItemType Directory -Force $d | Out-Null
    if ($spec) { $spec | ConvertTo-Json -Compress | Set-Content (Join-Path $d 'warm.json') }
    return $d
}
function Spec($idle) {
    return [ordered]@{ name = 'selftest'; exe = $Python; cwd = '{root}'; args = @('-m', 'http.server', "$Port", '--bind', '127.0.0.1')
                       mkdirs = @('{root}/made/a'); url = "http://127.0.0.1:$Port"; health = '/'; idle_s = $idle; max_age_s = 0 }
}
function WaitUp { for ($i = 0; $i -lt 60; $i++) { if (Test-WarmAnswers $script:Warm) { return $true }; Start-Sleep -Milliseconds 500 }; return $false }
try {
    $w1 = Work 'w1' (Spec 60)
    Use-Warm $w1
    Check 'first job: started' ($env:LAZY_WARM -eq 'started' -and $script:Warm)
    Check 'it answers' (WaitUp)
    Check 'mkdirs made' (Test-Path (Join-Path $Root 'made\a'))
    Check 'env url + pid + log' ($env:LAZY_WARM_URL -eq "http://127.0.0.1:$Port" -and [int]$env:LAZY_WARM_PID -eq $script:Warm.proc.Id -and (Test-Path $env:LAZY_WARM_LOG))
    $pid1 = $script:Warm.proc
    Use-Warm $w1
    Check 'same job again: reused, same process' ($env:LAZY_WARM -eq 'reused' -and $script:Warm.proc.Id -eq $pid1.Id)
    Use-Warm (Work 'w2' (Spec 61))
    Check 'other warm.json: old one stopped, new one started' ($env:LAZY_WARM -eq 'started' -and $pid1.HasExited -and $script:Warm.proc.Id -ne $pid1.Id)
    [void](WaitUp)
    $p2 = $script:Warm.proc
    Use-Warm (Work 'plain' $null)
    Check 'job without warm.json: stopped, env cleared' (-not $script:Warm -and $p2.HasExited -and -not $env:LAZY_WARM -and -not $env:LAZY_WARM_PID)
    Use-Warm $w1; [void](WaitUp)
    Stop-Process -Id $script:Warm.proc.Id -Force; Start-Sleep 1
    $dead = $script:Warm.proc.Id
    Use-Warm $w1
    Check 'a server that died: started again' ($env:LAZY_WARM -eq 'started' -and $script:Warm.proc.Id -ne $dead)
    [void](WaitUp)
    $p3 = $script:Warm.proc
    Watch-Warm
    Check 'watch: busy recently, kept' ($script:Warm -and -not $p3.HasExited)
    $script:Warm.last = (Get-Date).AddSeconds(-120)
    Watch-Warm
    Check 'watch: idle past idle_s, stopped' (-not $script:Warm -and $p3.HasExited)
    $bad = Spec 60; $bad.exe = 'no\such\python.exe'
    Use-Warm (Work 'bad' $bad)
    Check 'bad exe: no warm server, env cleared, no throw' (-not $script:Warm -and -not $env:LAZY_WARM)
    Check 'runner log says what happened' ((Get-Content $Log -Raw) -match 'warm selftest started' -and (Get-Content $Log -Raw) -match 'warm server not used')
} finally {
    Stop-Warm 'test ends'
    Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
}
if ($fails) { "$fails check(s) failed"; exit 1 }
'all warm checks passed'
