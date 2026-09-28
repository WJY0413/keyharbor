function Resolve-KeyHarborPython {
    param([string]$ProjectRoot)
    if ($env:KEYHARBOR_PYTHON) { return $env:KEYHARBOR_PYTHON }
    $localSettings = Join-Path $ProjectRoot 'tools/runtime.local.json'
    if (Test-Path -LiteralPath $localSettings) {
        $settings = Get-Content -LiteralPath $localSettings -Raw | ConvertFrom-Json
        if ($settings.python) { return $settings.python }
    }
    $projectPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $projectPython) { return $projectPython }
    throw 'Create the project .venv or set KEYHARBOR_PYTHON first.'
}
