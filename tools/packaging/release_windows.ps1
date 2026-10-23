[CmdletBinding()]
param(
    [string]$Python = '',
    [string]$QwenModelDir = ''
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonExe = if ($Python) { (Resolve-Path $Python).Path } else { Join-Path $root '.venv\Scripts\python.exe' }
$helper = Join-Path $root 'tools\packaging\release_windows.py'
$buildScript = Join-Path $root 'tools\packaging\build_onedir.py'
$smokeScript = Join-Path $root 'tools\diagnostics\packaged_api_smoke.py'
$releases = Join-Path $root 'releases'
$stage = $null
$published = [Collections.Generic.List[string]]::new()
Push-Location $root

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$Label) {
    & $Executable @Arguments
    $commandExit = $LASTEXITCODE
    & $pythonExe $helper preflight-exit --code $commandExit --phase $Label | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $commandExit" }
}

function Assert-OwnedStageDirectory([string]$Path, [string]$Parent) {
    $parentItem = Get-Item -LiteralPath $Parent -Force
    $targetItem = Get-Item -LiteralPath $Path -Force
    $reparsePoint = [IO.FileAttributes]::ReparsePoint
    if (($parentItem.Attributes -band $reparsePoint) -ne 0 -or ($targetItem.Attributes -band $reparsePoint) -ne 0) {
        throw 'Release staging directories must not be reparse points.'
    }
    $parentFull = [IO.Path]::GetFullPath($parentItem.FullName).TrimEnd('\') + '\'
    $targetFull = [IO.Path]::GetFullPath($targetItem.FullName)
    if (-not $targetFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Release staging path resolved outside the releases directory.'
    }
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw "Python 3.12.10 venv not found: $pythonExe" }
    $pythonVersion = (& $pythonExe -c 'import platform; print(platform.python_version())').Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.12.10') { throw "Expected Python 3.12.10, found '$pythonVersion'." }
    & $pythonExe -c 'import onnxruntime, PyInstaller; print("PyInstaller " + PyInstaller.__version__ + "; ONNX Runtime " + onnxruntime.__version__)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies are missing. Install the locked runtime and dev group with: uv sync --group dev --locked' }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { throw 'uv is required but was not found on PATH.' }
    $uvVersion = (& $uv.Source --version).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not read uv version: $uvVersion" }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'uv.lock') -PathType Leaf)) { throw 'uv.lock is missing.' }
    Invoke-Checked $uv.Source @('lock', '--check', '--offline') 'uv lock --check'

    $metadataText = & $pythonExe $helper metadata --root $root
    if ($LASTEXITCODE -ne 0) { throw 'Application version sources are inconsistent.' }
    $metadata = $metadataText | ConvertFrom-Json
    $version = [string]$metadata.version
    $sha = (& git -C $root rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $sha.Length -ne 40) { throw 'Could not determine the source Git SHA.' }
    $dirty = @(& git -C $root status --porcelain=v1 --untracked-files=all)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the Git worktree.' }
    if ($dirty.Count -gt 0) {
        $preview = ($dirty | Select-Object -First 12) -join [Environment]::NewLine
        throw "Release requires a clean committed worktree. Current changes:`n$preview"
    }

    $zipName = "PhotoSearch-v$version-win-x64.zip"
    $shaName = "$zipName.sha256"
    $reportName = "PhotoSearch-v$version-win-x64-report.json"
    $outputPaths = @($zipName, $shaName, $reportName) | ForEach-Object { Join-Path $releases $_ }
    Invoke-Checked $pythonExe (@($helper, 'preflight-output') + $outputPaths) 'release output preflight'

    $driveRoot = [IO.Path]::GetPathRoot($releases)
    $drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($driveRoot).TrimEnd('\').TrimEnd(':')) -ErrorAction SilentlyContinue
    if (-not $drive) { $drive = Get-PSDrive -Name (Split-Path -Qualifier $releases).TrimEnd(':') }
    $freeBytes = [long]$drive.Free
    $bundleEstimate = 11500000000L
    $zipEstimate = 6300000000L
    $knownBundle = Join-Path $root 'dist\PhotoSearch'
    if (Test-Path -LiteralPath $knownBundle -PathType Container) {
        $measured = (Get-ChildItem -LiteralPath $knownBundle -Recurse -File | Measure-Object -Property Length -Sum).Sum
        if ($measured -gt 0) { $bundleEstimate = [long]$measured }
        $zipEstimate = [long]($bundleEstimate * 0.56)
    }
    $requiredBytes = [long]([Math]::Ceiling($bundleEstimate * 1.75 + $zipEstimate + 1GB))
    try {
        Invoke-Checked $pythonExe @($helper, 'preflight-space', '--free', "$freeBytes", '--required', "$requiredBytes") 'disk space preflight'
    } catch {
        throw ("Insufficient disk space on {0}: available {1:N2} GiB, estimated need {2:N2} GiB. {3}" -f $drive.Name, ($freeBytes / 1GB), ($requiredBytes / 1GB), $_)
    }

    New-Item -ItemType Directory -Path $releases -Force | Out-Null
    $repoPrefix = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    $releasesItem = Get-Item -LiteralPath $releases -Force
    if (($releasesItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or -not $releasesItem.FullName.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The releases output directory must be a regular directory inside the repository.'
    }
    $stage = Join-Path $releases ('.release-' + [Guid]::NewGuid().ToString('N'))
    $dist = Join-Path $stage 'dist'
    $work = Join-Path $stage 'work'
    $bundle = Join-Path $dist 'PhotoSearch'
    $zipPart = Join-Path $stage ($zipName + '.part')
    New-Item -ItemType Directory -Path $stage | Out-Null

    $buildStarted = Get-Date
    $buildTimer = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Checked $pythonExe @($buildScript, '--distpath', $dist, '--workpath', $work) 'PyInstaller build'
    $buildTimer.Stop()
    Invoke-Checked $pythonExe @($helper, 'validate-bundle', '--bundle', $bundle) 'onedir validation'

    $smokeWork = Join-Path $stage 'smoke-work'
    $smokeOutput = Join-Path $stage 'packaged-smoke.json'
    Invoke-Checked $pythonExe @($smokeScript, '--bundle', $bundle, '--in-place', '--work-dir', $smokeWork, '--output', $smokeOutput) 'packaged API smoke'
    $smoke = Get-Content -LiteralPath $smokeOutput -Raw | ConvertFrom-Json
    if ($smoke.status -ne 'ok' -or $smoke.checks.in_place_frozen_run -ne $true) { throw 'Packaged API smoke did not pass in-place.' }

    $gpuSummary = $null
    $gpuValidation = 'GPU_NOT_TESTED'
    if ($QwenModelDir) {
        if (-not (Test-Path -LiteralPath $QwenModelDir -PathType Container)) { throw 'The explicitly supplied Qwen model directory does not exist.' }
        $gpuOutput = Join-Path $stage 'qwen-gpu-smoke.json'
        $manifest = Join-Path $bundle '_internal\tools\prepare_models\manifests\qwen3-vl-embedding-2b-exif-v2.json'
        $executable = Join-Path $bundle 'PhotoSearch.exe'
        $oldHubOffline = $env:HF_HUB_OFFLINE
        $oldTransformersOffline = $env:TRANSFORMERS_OFFLINE
        $oldTemp = $env:TEMP
        $oldTmp = $env:TMP
        try {
            $env:HF_HUB_OFFLINE = '1'
            $env:TRANSFORMERS_OFFLINE = '1'
            $env:TEMP = $smokeWork
            $env:TMP = $smokeWork
            Invoke-Checked $executable @('demo-search', '--manifest', $manifest, '--model-dir', $QwenModelDir, '--device', 'cuda', '--device-policy', 'gpu_required', '--output', $gpuOutput) 'frozen Qwen CUDA smoke'
        } finally {
            $env:HF_HUB_OFFLINE = $oldHubOffline
            $env:TRANSFORMERS_OFFLINE = $oldTransformersOffline
            $env:TEMP = $oldTemp
            $env:TMP = $oldTmp
        }
        $gpuSummaryText = & $pythonExe $helper gpu-summary --path $gpuOutput
        if ($LASTEXITCODE -ne 0) { throw 'Frozen Qwen GPU report did not meet required CUDA checks.' }
        $gpuSummary = $gpuSummaryText | ConvertFrom-Json
        $gpuValidation = 'GPU_VERIFIED'
    }

    $archiveJson = & $pythonExe $helper archive --bundle $bundle --part $zipPart
    if ($LASTEXITCODE -ne 0) { throw 'ZIP creation or integrity verification failed.' }
    $archive = $archiveJson | ConvertFrom-Json
    $hashText = "$($archive.sha256) *$zipName`n"
    $report = [ordered]@{
        status = if ($gpuValidation -eq 'GPU_VERIFIED') { 'LOCAL_PACKAGE_PASS_GPU_VERIFIED' } else { 'LOCAL_PACKAGE_PASS_GPU_NOT_TESTED' }
        version = $version
        git_sha = $sha
        python_version = $pythonVersion
        uv_version = $uvVersion
        built_at_utc = $buildStarted.ToUniversalTime().ToString('o')
        build_exit_code = 0
        build_seconds = [Math]::Round($buildTimer.Elapsed.TotalSeconds, 1)
        packaged_smoke = 'PASS'
        packaged_smoke_exit_code = 0
        gpu_validation = $gpuValidation
        gpu_smoke = $gpuSummary
        limitations = if ($gpuValidation -eq 'GPU_VERIFIED') { @('One local RTX/CUDA smoke only; no second clean Windows PC was tested.') } else { @('GPU not tested; package launchability is not a Qwen GPU compatibility claim.', 'No second clean Windows PC was tested.') }
        zip = $zipName
        zip_bytes = [long]$archive.zip_bytes
        sha256 = [string]$archive.sha256
        entries = [int]$archive.entries
        suitable_for_sharing = $false
    }
    $reportPart = Join-Path $stage $reportName
    $shaPart = Join-Path $stage $shaName
    [IO.File]::WriteAllText($reportPart, (($report | ConvertTo-Json -Depth 8) + "`n"), [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($shaPart, $hashText, [Text.UTF8Encoding]::new($false))
    $zipOutput = Join-Path $releases $zipName
    $shaOutput = Join-Path $releases $shaName
    $reportOutput = Join-Path $releases $reportName
    [IO.File]::Move($shaPart, $shaOutput)
    $published.Add($shaOutput)
    [IO.File]::Move($reportPart, $reportOutput)
    $published.Add($reportOutput)
    [IO.File]::Move($zipPart, $zipOutput)
    $published.Add($zipOutput)
    Assert-OwnedStageDirectory $stage $releases
    Remove-Item -LiteralPath $stage -Recurse -Force
    $stage = $null
    Write-Host "Version: $version"
    Write-Host "Git SHA: $sha"
    Write-Host "ZIP: $(Join-Path $releases $zipName) ($($archive.zip_bytes) bytes)"
    Write-Host "SHA-256: $($archive.sha256)"
    Write-Host "packaged_smoke: PASS; gpu_smoke: $gpuValidation"
    if ($gpuValidation -eq 'GPU_NOT_TESTED') { Write-Host 'Package can start; Qwen GPU was not tested.' }
    Write-Host 'This is a personal candidate package.'
    Write-Host "Report: $(Join-Path $releases $reportName)"
} catch {
    foreach ($path in $published) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    Write-Error $_
    exit 1
} finally {
    Pop-Location
}
