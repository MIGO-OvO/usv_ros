param(
    [string]$Address,
    [string]$Bundle,
    [string]$User = 'jetson',
    [string]$RemoteWorkspace = 'usv_ws',
    [string]$Destination = (Join-Path $PSScriptRoot '../../../.usv_run/downloads')
)
$ErrorActionPreference = 'Stop'

function Get-LogRemotePath {
    param([string]$Address, [string]$Bundle, [string]$User, [string]$RemoteWorkspace)
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) {
        throw 'Enter a valid Jetson IP address.'
    }
    if ($User -cnotmatch '^[a-z_][a-z0-9_-]*$' -or
        $RemoteWorkspace -cnotmatch '^/?[a-zA-Z0-9_/-]+$' -or
        $RemoteWorkspace.Split('/') -contains '..') {
        throw 'Invalid user or remote workspace.'
    }
    $name = $Bundle -creplace '\.tar\.gz$', ''
    if ($name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$') {
        throw 'Bundle name must contain 1-80 letters, digits, dots, underscores or hyphens.'
    }
    $hostPart = $parsed.ToString()
    if ($parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
        $hostPart = "[$hostPart]"
    }
    return "${User}@${hostPart}:$RemoteWorkspace/.usv_run/bundles/$name.tar.gz"
}

function Receive-JetsonLogs {
    param([string]$Address, [string]$Bundle, [string]$User, [string]$RemoteWorkspace, [string]$Destination)
    $remote = Get-LogRemotePath $Address $Bundle $User $RemoteWorkspace
    Get-Command scp -ErrorAction Stop | Out-Null
    $folder = [System.IO.Directory]::CreateDirectory($Destination).FullName
    $name = ($Bundle -creplace '\.tar\.gz$', '') + '.tar.gz'
    $target = Join-Path $folder $name
    if (Test-Path -LiteralPath $target) { throw "File already exists: $target" }
    $partial = Join-Path $folder ([guid]::NewGuid().ToString() + '.partial')
    try {
        # Keep OpenSSH host-key verification and password/key authentication.
        & scp -- $remote $partial
        if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)." }
        if (-not (Test-Path -LiteralPath $partial) -or (Get-Item -LiteralPath $partial).Length -eq 0) {
            throw 'scp did not produce a non-empty bundle.'
        }
        [System.IO.File]::Move($partial, $target)
        Write-Host "Downloaded: $target"
    } finally {
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if (-not $Address) { $Address = Read-Host 'Jetson IP address' }
        if (-not $Bundle) { $Bundle = Read-Host 'Bundle name (created by usvlogs pack)' }
        Receive-JetsonLogs $Address $Bundle $User $RemoteWorkspace $Destination
    } catch {
        Write-Error $_
        exit 1
    }
}
