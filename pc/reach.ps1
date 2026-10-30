# reach.ps1 - the places outside the work folder that the job account might reach: other fixed drives, and
# folders straight under C:\. One list for two scripts (dot-source it):
#   check.ps1     runs as the job account and tests what it can really read or change there (reports only)
#   setup-pc.ps1  adds a DENY for the job account alone on each one, so it can't (Undo-PC removes them)
# Left open on purpose: the work folder, your extra model folders (and the folders that hold them), what the
# job runner needs (Windows, Program Files, ProgramData, Users) and any folder on the system PATH.

$ReachSkipTop = @('Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', 'Users', 'Recovery', 'PerfLogs',
    '$Recycle.Bin', 'System Volume Information', '$WinREAgent', '$SysReset', '$Windows.~BT', '$Windows.~WS', 'Windows.old',
    'WindowsApps', 'WpSystem')   # Store apps moved to another drive

function Get-ReachTargets([string[]]$OpenFolders = @()) {
    $norm = { param($p) ([IO.Path]::GetFullPath(($p -replace '/', '\'))).TrimEnd('\') + '\' }
    $open = @($OpenFolders | Where-Object { $_ } | ForEach-Object { & $norm $_ })
    # Folders on the system PATH (an ffmpeg or Python installed at C:\tools\...) stay reachable.
    $open += @(([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') | Where-Object { $_ -match '^[A-Za-z]:\\' } |
        ForEach-Object { try { & $norm $_ } catch { } })
    # A place stays open if an open folder is inside it (locking it would lock that folder too) or it is inside one.
    $isOpen = { param($p) $pp = & $norm $p; [bool]($open | Where-Object { $_.StartsWith($pp, 'OrdinalIgnoreCase') -or $pp.StartsWith($_, 'OrdinalIgnoreCase') }) }
    $sys = & $norm $env:SystemDrive
    foreach ($d in [IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady }) {
        $root = $d.RootDirectory.FullName
        if ($root -ine $sys -and -not (& $isOpen $root)) { $root; continue }    # a whole other drive
        Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $ReachSkipTop -notcontains $_.Name -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
            Where-Object { -not (& $isOpen $_.FullName) } | ForEach-Object { $_.FullName }
    }
}
