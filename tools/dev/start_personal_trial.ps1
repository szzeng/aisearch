$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DataRoot = "D:\SZ\project\PhotoSearchData"

if (-not (Test-Path $Python)) {
    throw "Python environment not found: $Python"
}

if (-not (Test-Path $DataRoot)) {
    throw "PhotoSearch data root not found: $DataRoot"
}

Push-Location $RepoRoot
try {
    $Head = (& git rev-parse --short HEAD 2>$null)
    Write-Host "PhotoSearch personal trial"
    Write-Host "HEAD:      $Head"
    Write-Host "Data root: $DataRoot"
    Write-Host ""

    & $Python -m photo_search `
        --data-root $DataRoot `
        --model-device cuda `
        --open-browser

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
