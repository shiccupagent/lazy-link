<#
install-windows.ps1 - the Windows half of Lazy Link setup, for a Windows 10/11 computer that will
control the big PC. The twin of install-mac.sh. Safe to run again. Needs no admin rights.

It makes this computer's own SSH key for the PC link (no passphrase), puts the public half where
the PC setup finds it, copies it to the clipboard, and tells you the next step. Nothing is sent
anywhere, and the private key never leaves this computer.

Run it by double-clicking Install-Windows.cmd, or from a PowerShell window in this folder:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
#>
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Native tools (ssh-keygen, python) print notes to stderr. Windows PowerShell 5.1 turns those into
# errors under 'Stop', so run them with 'Continue' and judge them by their exit code only.
function Invoke-Tool([string]$exe, [string[]]$argList) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $exe @argList 2>&1
        $code = $LASTEXITCODE
    } catch {
        # a broken shim (for example a python.bat this account cannot run) throws instead of giving an exit code
        $out = @($_.Exception.Message); $code = -1
    } finally { $ErrorActionPreference = $saved }
    [pscustomobject]@{ Code = $code; Text = ((@($out) | ForEach-Object { "$_" }) -join "`n").Trim() }
}

try {
    # 1. Python 3.9 or newer: the Python launcher (py) or python on PATH. Never installed silently.
    $pyWord = $null; $pyText = $null
    $candidates = @(
        [pscustomobject]@{ Exe = 'py';      Args = @('-3') },
        [pscustomobject]@{ Exe = 'python';  Args = @() },
        [pscustomobject]@{ Exe = 'python3'; Args = @() })
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $r = Invoke-Tool $c.Exe ($c.Args + @('-c', 'import sys; print(sys.version.split()[0]); sys.exit(0 if sys.version_info >= (3, 9) else 1)'))
        if ($r.Code -eq 0) { $pyWord = $c.Exe; $pyText = $r.Text; break }
    }

    # 2. OpenSSH client tools (built into Windows 10/11, optional feature).
    $needTools = @()
    foreach ($t in 'ssh', 'ssh-keygen', 'scp', 'ssh-keyscan') {
        if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { $needTools += $t }
    }

    $problems = @()
    if (-not $pyWord) {
        $problems += 'Python 3.9 or newer was not found (or the one found is older than 3.9).'
        $problems += '  Install it with this one line, then run this again:'
        $problems += '    winget install Python.Python.3.12'
    }
    if ($needTools) {
        $problems += "The OpenSSH Client is missing ($($needTools -join ', ')). Turn it on: Settings > System > Optional features >"
        $problems += '  Add a feature > OpenSSH Client. Windows may ask for an administrator password for that one step.'
    }
    if ($problems) {
        Write-Host ''
        $problems | ForEach-Object { Write-Host $_ }
        Write-Host ''
        Write-Host 'Nothing was changed. Fix the line(s) above, then run this again.'
        exit 1
    }
    Write-Host "Python $pyText found ($pyWord). OpenSSH tools found."

    # 3. This computer's key. Folder and key names match install-mac.sh and pcjob (~/.ssh/lazy-link).
    $sshDir = Join-Path $env:USERPROFILE '.ssh'
    $key = Join-Path $sshDir 'lazy-link'
    $oldKey = Join-Path $sshDir 'render-link'   # the name before 2026-10-29: keep using that key
    New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
    if (-not (Test-Path $key) -and (Test-Path $oldKey)) {
        Move-Item -LiteralPath $oldKey -Destination $key
        if (Test-Path "$oldKey.pub") { Move-Item -LiteralPath "$oldKey.pub" -Destination "$key.pub" }
    }
    if (-not (Test-Path $key)) {
        # '""' is the literal empty passphrase: Windows PowerShell drops a bare '' argument.
        $r = Invoke-Tool 'ssh-keygen' @('-q', '-t', 'ed25519', '-N', '""', '-C', 'lazy-link', '-f', $key)
        if ($r.Code -ne 0 -or -not (Test-Path $key)) { throw "ssh-keygen failed (exit $($r.Code)): $($r.Text)" }
        $check = Invoke-Tool 'ssh-keygen' @('-y', '-f', $key)
        if ($check.Code -ne 0) {
            Remove-Item -LiteralPath $key, "$key.pub" -Force -ErrorAction SilentlyContinue
            throw 'The new key came out with a passphrase, which Lazy Link does not use. Nothing was kept; run this again.'
        }
        Write-Host "Made a new key for the PC link: $key (it never leaves this computer)"
    } else {
        Write-Host "Key already there: $key"
    }
    if (-not (Test-Path "$key.pub")) {
        $pub = Invoke-Tool 'ssh-keygen' @('-y', '-f', $key)
        if ($pub.Code -ne 0) { throw "could not read the public key from $key" }
        Set-Content -LiteralPath "$key.pub" -Value $pub.Text -Encoding ASCII
    }

    # 4. Put the PUBLIC half where the PC setup reads it (pc\mac-key.pub, gitignored).
    $pcDir = Join-Path $PSScriptRoot 'pc'
    New-Item -ItemType Directory -Force -Path $pcDir | Out-Null
    Copy-Item -LiteralPath "$key.pub" -Destination (Join-Path $pcDir 'mac-key.pub') -Force
    Copy-Item -LiteralPath "$key.pub" -Destination (Join-Path $PSScriptRoot 'linux\mac-key.pub') -Force   # for a Linux big PC
    $pubLine = (Get-Content -LiteralPath "$key.pub" -Raw).Trim()

    # 5. Clipboard (best effort: some sessions have no clipboard; the key is printed below anyway).
    try { Set-Clipboard -Value $pubLine; $copied = ' (also copied to the clipboard)' } catch { $copied = '' }

    # 6. What to do next. The commands use the launcher that worked here.
    $run = $pyWord
    Write-Host @"

Controller side ready. Next, on the gaming PC (the big one with the NVIDIA card):
  1. Copy this whole folder to that PC (USB stick, or download the same repo there).
     pc\mac-key.pub is this computer's PUBLIC key: safe to copy; the PC setup reads it from there.
  2. Double-click pc\Setup-PC.cmd and say Yes to the admin prompt.
     (No copy of the folder? Setup asks you to paste this line${copied}:)

$pubLine

  3. Back here, run the command the PC shows at the end, for example:
       $run mac\pcjob pair 192.168.1.50
  4. See what the PC can run, and what to fix first:
       $run check.py
     then install exactly what it recommends:
       $run check.py --setup
"@
} catch {
    Write-Host ''
    Write-Host "install-windows: $($_.Exception.Message)"
    Write-Host 'Nothing else was changed. Fix that, then run this again.'
    exit 1
}
