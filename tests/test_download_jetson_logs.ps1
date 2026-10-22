$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/../scripts/download_jetson_logs.ps1"
$actual = Get-LogRemotePath '192.168.1.2' 'sample.tar.gz' 'jetson' 'usv_ws'
if ($actual -ne 'jetson@192.168.1.2:usv_ws/.usv_run/bundles/sample.tar.gz') { throw $actual }
foreach ($bad in @('../escape', 'a;touch x', '-option', '')) {
    $rejected = $false
    try { Get-LogRemotePath '192.168.1.2' $bad 'jetson' 'usv_ws' } catch { $rejected = $true }
    if (-not $rejected) { throw "Accepted unsafe name: $bad" }
}
$folder = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
function scp {
    [System.IO.File]::WriteAllText($args[-1], 'test bundle')
    $global:LASTEXITCODE = 0
}
try {
    Receive-JetsonLogs '192.168.1.2' 'sample' 'jetson' 'usv_ws' $folder
    if (-not (Test-Path -LiteralPath (Join-Path $folder 'sample.tar.gz'))) { throw 'Missing download' }
    $rejected = $false
    try { Receive-JetsonLogs '192.168.1.2' 'sample' 'jetson' 'usv_ws' $folder } catch { $rejected = $true }
    if (-not $rejected) { throw 'Existing bundle overwritten' }
    function scp { $global:LASTEXITCODE = 1 }
    $rejected = $false
    try { Receive-JetsonLogs '192.168.1.2' 'failed' 'jetson' 'usv_ws' $folder } catch { $rejected = $true }
    if (-not $rejected -or (Test-Path -LiteralPath (Join-Path $folder 'failed.tar.gz'))) { throw 'Failure reported as success' }
    Write-Host 'PASS: input validation, download, overwrite refusal, scp failure'
} finally {
    # Delete only the known test file; the unique test directory must be empty.
    Remove-Item -LiteralPath (Join-Path $folder 'sample.tar.gz') -ErrorAction SilentlyContinue
    [System.IO.Directory]::Delete($folder)
}
