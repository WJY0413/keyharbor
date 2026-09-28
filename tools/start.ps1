param([switch]$WithWatchdog, [switch]$WithLegacyBridge, [switch]$Open)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$runtimePython = Resolve-KeyHarborPython $projectRoot
$registry = Get-Content -LiteralPath (Join-Path $projectRoot 'registry.json') -Raw | ConvertFrom-Json
$endpoint = "http://127.0.0.1:$($registry.port)/healthz"
$health = $null
try { $health = Invoke-RestMethod $endpoint -TimeoutSec 2 } catch {}
if ($health -and $health.service -ne 'cooper-litellm') { throw 'Port belongs to another service' }
if (-not $health) {
    $service = Start-Process -FilePath $runtimePython -ArgumentList '-m keyharbor serve' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 1
        try { $health = Invoke-RestMethod $endpoint -TimeoutSec 1 } catch {}
        if ($health.ready) { break }
        if ($service.HasExited) { throw 'Service startup failed; old routes are unchanged' }
    }
    if (-not $health.ready) { throw 'Service readiness timed out; inspect status before selecting managed mode' }
}
if ($WithWatchdog) {
    $watchers = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m (cooper_litellm|keyharbor) watchdog' })
    if ($watchers.Count -eq 0) {
        Start-Process -FilePath $runtimePython -ArgumentList "-m keyharbor watchdog --port $($registry.port)" -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    }
}
try { $panel = Invoke-WebRequest 'http://127.0.0.1:4104/' -TimeoutSec 2 } catch { $panel = $null }
if (-not $panel) {
    Start-Process -FilePath $runtimePython -ArgumentList '-m keyharbor admin' -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
}
if ($WithLegacyBridge) {
    $bridgePort = if ($registry.legacy_port) { [int]$registry.legacy_port } else { 4105 }
    $bridgeHealth = $null
    try { $bridgeHealth = Invoke-RestMethod "http://127.0.0.1:$bridgePort/healthz" -TimeoutSec 2 } catch {}
    if ($bridgeHealth -and $bridgeHealth.implementation -ne 'keyharbor') { throw 'Compatibility port belongs to another service' }
    if (-not $bridgeHealth) {
        Start-Process -FilePath $runtimePython -ArgumentList "-m keyharbor legacy-bridge --port $bridgePort" -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    }
}
if ($Open) { Start-Process 'http://127.0.0.1:4104/' }
[pscustomobject]@{ service = $health.service; ready = $health.ready; port = $registry.port; watchdogRequested = [bool]$WithWatchdog } | ConvertTo-Json -Compress
