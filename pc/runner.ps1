<#
runner.ps1 - the PC job runner. setup-pc.ps1 copies it to C:\render\runner\runner.ps1 and runs it
as the hidden 'render' account from the LazyLink\LazyRunner scheduled task, outside any SSH
session, so a dropped link never kills a job.

A job is a folder C:\render\jobs\<id>\ that the Mac fills (mac/pcjob):
  in.tar    the job folder from the Mac; must contain job.ps1
  job.json  {"timeout_s": 3600}
  ready     written last; the runner only picks up folders that have it
The runner unpacks in.tar into work\, runs job.ps1 there (env RENDER_OUT = the out\ folder,
RENDER_ROOT = C:\render, FFMPEG and BLENDER when found), keeps the PC awake while it runs, kills
the whole process tree on timeout or when a 'cancel' file appears, then packs out\ into out.tar.
status.json is the state: queued -> running -> done | failed | timeout | cancelled.
One job at a time, oldest first. When this file changes on disk the runner exits and the task's
loop starts the new copy (that is how `pcjob push-runner` updates it).
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path (Split-Path $MyInvocation.MyCommand.Path)
$Jobs = Join-Path $Root 'jobs'
$Log = Join-Path $Root 'runner.log'
$Beat = Join-Path $Root 'runner.heartbeat'
$Self = $MyInvocation.MyCommand.Path
$SelfHash = (Get-FileHash $Self).Hash
$Ps = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$Tar = "$env:WINDIR\System32\tar.exe"

# Tools a job may use, found once at start (render can only use what it can read).
$env:RENDER_ROOT = $Root
$ff = @((Join-Path $Root 'tools\ffmpeg\bin\ffmpeg.exe'), 'C:\Program Files\ffmpeg\bin\ffmpeg.exe', 'C:\Program Files\Kdenlive\bin\ffmpeg.exe') |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ff) { $ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }
if ($ff) { $env:FFMPEG = $ff }
$bl = Get-ChildItem 'C:\Program Files\Blender Foundation\Blender *\blender.exe' -ErrorAction SilentlyContinue |
    Sort-Object { [version](($_.Directory.Name -replace '[^0-9.]', '') + '.0') } -Descending | Select-Object -First 1
if ($bl) { $env:BLENDER = $bl.FullName }

# Each job runs inside a Windows job object: TerminateJobObject ends the job and everything
# it started (render may not use taskkill or WMI), and KILL_ON_JOB_CLOSE means a runner that
# dies takes its job with it instead of leaving an orphan holding the GPU.
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
namespace LazyLink {
public static class Native {
    [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attrs, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int cls, IntPtr info, uint len);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
    [DllImport("kernel32.dll")] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
    public static IntPtr NewKillOnCloseJob() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Exception("CreateJobObject failed " + Marshal.GetLastWin32Error());
        int size = IntPtr.Size == 8 ? 144 : 112;   // JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        IntPtr buf = Marshal.AllocHGlobal(size);
        try {
            for (int i = 0; i < size; i++) Marshal.WriteByte(buf, i, 0);
            Marshal.WriteInt32(buf, 16, 0x2000);    // BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(job, 9, buf, (uint)size)) throw new Exception("SetInformationJobObject failed " + Marshal.GetLastWin32Error());
        } finally { Marshal.FreeHGlobal(buf); }
        return job;
    }
    public static void Assign(IntPtr job, IntPtr proc) {
        if (!AssignProcessToJobObject(job, proc)) throw new Exception("AssignProcessToJobObject failed " + Marshal.GetLastWin32Error());
    }
    public static bool Kill(IntPtr job) { return TerminateJobObject(job, 1); }
}
}
'@
$AWAKE = [uint32]2147483649   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
$RELEASE = [uint32]2147483648 # ES_CONTINUOUS

# Native tools write warnings to stderr; under 'Stop' PowerShell 5.1 turns those into
# exceptions, so every native call goes through here and is judged by its exit code only.
function Invoke-Native {
    $ErrorActionPreference = 'Continue'
    $exe, $rest = $args
    & $exe @rest 2>&1 | Out-Null
    return $LASTEXITCODE
}
function Write-Log($msg) {
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 1MB) { Move-Item $Log "$Log.1" -Force }
    Add-Content $Log ("{0} {1}" -f (Get-Date -Format s), $msg)
}
function Set-Status($dir, $st) {
    $st.updated = Get-Date -Format o
    $tmp = Join-Path $dir 'status.tmp'
    $st | ConvertTo-Json | Set-Content $tmp
    Move-Item $tmp (Join-Path $dir 'status.json') -Force
}
function Invoke-Job($dir) {
    $id = Split-Path $dir -Leaf
    $timeout = 3600
    try { $timeout = [int](Get-Content (Join-Path $dir 'job.json') -Raw | ConvertFrom-Json).timeout_s } catch {}
    $st = @{ id = $id; state = 'running'; started = (Get-Date -Format o); timeout_s = $timeout; pid = $null; exit = $null; seconds = $null }
    Set-Status $dir $st
    Write-Log "start $id (timeout ${timeout}s)"
    $work = Join-Path $dir 'work'; $out = Join-Path $dir 'out'
    New-Item -ItemType Directory -Force $work, $out | Out-Null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $jobObj = [IntPtr]::Zero
    try {
        $rc = Invoke-Native $Tar -xf (Join-Path $dir 'in.tar') -C $work
        if ($rc -ne 0) { throw "tar unpack failed ($rc)" }
        if (-not (Test-Path (Join-Path $work 'job.ps1'))) { throw 'job.ps1 missing from in.tar' }
        $env:RENDER_JOB = $id; $env:RENDER_OUT = $out
        $p = Start-Process $Ps -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $work 'job.ps1') `
            -WorkingDirectory $work -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $dir 'log.txt') -RedirectStandardError (Join-Path $dir 'err.txt')
        # Assigned right after start: powershell.exe takes far longer to load than this, so
        # job.ps1 cannot have started a child outside the job object yet.
        $jobObj = [LazyLink.Native]::NewKillOnCloseJob()
        [LazyLink.Native]::Assign($jobObj, $p.Handle)   # reading Handle also keeps the exit code readable
        $st.pid = $p.Id; Set-Status $dir $st
        [void][LazyLink.Native]::SetThreadExecutionState($AWAKE)
        $end = 'done'
        while (-not $p.WaitForExit(2000)) {
            Set-Content $Beat (Get-Date -Format o)
            $why = if (Test-Path (Join-Path $dir 'cancel')) { 'cancelled' } elseif ($clock.Elapsed.TotalSeconds -gt $timeout) { 'timeout' }
            if ($why) {
                [void][LazyLink.Native]::Kill($jobObj)
                $p.WaitForExit(10000) | Out-Null
                $end = $why; break
            }
        }
        $st.exit = $p.ExitCode
        if ($end -eq 'done' -and $p.ExitCode -ne 0) { $end = 'failed' }
    } catch {
        $end = 'failed'; $st.error = "$_"
    } finally {
        [void][LazyLink.Native]::SetThreadExecutionState($RELEASE)
        # Closing the job object also ends anything job.ps1 left running in the background.
        if ($jobObj -ne [IntPtr]::Zero) { [void][LazyLink.Native]::CloseHandle($jobObj) }
    }
    if ((Invoke-Native $Tar -cf (Join-Path $dir 'out.tar') -C $out .) -ne 0) { $st.error = "$($st.error) packing out.tar failed".Trim() }
    $st.state = $end; $st.seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1); $st.ended = Get-Date -Format o
    Set-Status $dir $st
    Write-Log "end $id $end exit=$($st.exit) $($st.seconds)s"
}

New-Item -ItemType Directory -Force $Jobs | Out-Null
# One runner at a time: the lock file stays open (unshared) for this runner's whole life.
try { $lock = [IO.File]::Open((Join-Path $Root 'runner.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
catch { Write-Log "another runner holds runner.lock; pid $PID exits"; Start-Sleep 30; exit 0 }
Write-Log "runner up (pid $PID)"
# A job left 'running' by a crash or reboot is marked failed, never silently rerun.
Get-ChildItem $Jobs -Directory | ForEach-Object {
    $sf = Join-Path $_.FullName 'status.json'
    if ((Test-Path $sf) -and ((Get-Content $sf -Raw | ConvertFrom-Json).state -eq 'running')) {
        $st = @{}; (Get-Content $sf -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $st[$_.Name] = $_.Value }
        $st.state = 'failed'; $st.error = 'runner restarted while this job was running'
        Set-Status $_.FullName $st
    }
}
while ($true) {
    Set-Content $Beat (Get-Date -Format o)
    if ((Get-FileHash $Self).Hash -ne $SelfHash) { Write-Log 'runner.ps1 changed; restarting'; exit 0 }
    $next = Get-ChildItem $Jobs -Directory |
        Where-Object { (Test-Path (Join-Path $_.FullName 'ready')) -and -not (Test-Path (Join-Path $_.FullName 'status.json')) } |
        Sort-Object CreationTime | Select-Object -First 1
    if (-not $next) { Start-Sleep 2; continue }
    try { Invoke-Job $next.FullName }
    catch {
        Write-Log "runner error on $($next.Name): $_"
        try { Set-Status $next.FullName @{ id = $next.Name; state = 'failed'; error = "runner error: $_" } } catch {}
    }
}
