param(
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'resolve-python.ps1')
$runtimePython = Resolve-KeyHarborPython $projectRoot
Push-Location $projectRoot
try {
    & $runtimePython -m cooper_litellm @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
