# Windows PowerShell 5.1 compatible. Saved as UTF-8 BOM for Chinese prompts.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Start', 'Download', 'Configure')]
    [string]$Mode,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AdditionalArguments
)

$ErrorActionPreference = 'Stop'
$script:AppDirectory = [IO.Path]::GetFullPath($PSScriptRoot)
$script:Exe = Join-Path $script:AppDirectory 'PhotoSearch.exe'
$script:ConfigDirectory = Join-Path $env:APPDATA 'PhotoSearch'
$script:ConfigPath = Join-Path $script:ConfigDirectory 'portable-data-root.txt'

function Normalize-DataRoot([string]$Value) {
    $raw = $Value.Trim().Trim('"')
    if ($raw -notmatch '^[A-Za-z]:\\') {
        throw '请输入完整的本地磁盘路径，例如 D:\PhotoSearchData。暂不支持相对路径或网络共享。'
    }
    $path = [IO.Path]::GetFullPath($raw).TrimEnd('\')
    $volumeRoot = [IO.Path]::GetPathRoot($path).TrimEnd('\')
    if ($path.Equals($volumeRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw '请在磁盘下选择专用文件夹，不要直接使用整个磁盘根目录。'
    }
    $install = $script:AppDirectory.TrimEnd('\')
    if ($path.Equals($install, [StringComparison]::OrdinalIgnoreCase) -or
        $path.StartsWith($install + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '数据目录不能位于应用解压目录内。请使用其他目录，例如 D:\PhotoSearchData。'
    }
    return $path
}

function Read-SavedRoot {
    if (-not (Test-Path -LiteralPath $script:ConfigPath -PathType Leaf)) { return $null }
    $content = [IO.File]::ReadAllText($script:ConfigPath, [Text.Encoding]::UTF8)
    if ([string]::IsNullOrWhiteSpace($content)) {
        throw "保存的路径配置为空：$script:ConfigPath。请运行 Set-PhotoSearch-Data.cmd 重新选择。"
    }
    return (Normalize-DataRoot $content)
}

function Save-Root([string]$Root) {
    [IO.Directory]::CreateDirectory($script:ConfigDirectory) | Out-Null
    $temporary = Join-Path $script:ConfigDirectory ('portable-data-root-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($temporary, $Root + "`r`n", [Text.UTF8Encoding]::new($true))
        Move-Item -LiteralPath $temporary -Destination $script:ConfigPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Select-NewRoot([string]$Previous) {
    Write-Host ''
    Write-Host 'PhotoSearch 数据目录设置' -ForegroundColor Cyan
    Write-Host '模型、索引、缩略图及数据库都将存放在此目录，不会默认占用 C 盘。'
    Write-Host '示例：D:\PhotoSearchData  或  E:\MyPhotos\PhotoSearchData'
    if ($Previous) {
        Write-Host "当前目录：$Previous" -ForegroundColor Yellow
        Write-Host '选择新目录不会移动旧数据。请先停止应用，必要时自行备份或迁移。' -ForegroundColor Yellow
    } elseif ($env:LOCALAPPDATA) {
        $legacy = Join-Path $env:LOCALAPPDATA 'PhotoSearchData'
        if (Test-Path -LiteralPath $legacy -PathType Container) {
            Write-Host "检测到旧版默认数据目录：$legacy。不会自动迁移或删除。" -ForegroundColor Yellow
        }
    }
    $inputPath = Read-Host '请输入完整数据目录（直接回车取消）'
    if ([string]::IsNullOrWhiteSpace($inputPath)) { throw '已取消：未创建或切换数据目录。' }
    $chosen = Normalize-DataRoot $inputPath
    if (-not (Test-Path -LiteralPath $chosen -PathType Container)) {
        $answer = Read-Host "目录不存在，将创建 $chosen。确认请输入 Y（其他输入取消）"
        if ($answer -ne 'Y') { throw '已取消：未创建数据目录。' }
        [IO.Directory]::CreateDirectory($chosen) | Out-Null
    }
    # Verify the selected volume is writable before saving it. Never touch user photos.
    $probe = Join-Path $chosen ('.photos-search-write-check-' + [Guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllText($probe, 'ok')
    } finally {
        if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
    }
    Save-Root $chosen
    Write-Host "已保存数据目录：$chosen" -ForegroundColor Green
    return $chosen
}

try {
    if (-not $env:APPDATA) { throw '无法找到 Windows APPDATA 配置目录。' }
    if ($Mode -ne 'Configure' -and -not (Test-Path -LiteralPath $script:Exe -PathType Leaf)) {
        throw "找不到 PhotoSearch.exe：$script:Exe。请先完整解压 ZIP。"
    }
    if ($Mode -eq 'Start') {
        foreach ($argument in @($AdditionalArguments)) {
            if ($argument -match '^--data-root(?:=|$)') {
                throw '启动器统一使用已保存的数据目录。要更改请运行 Set-PhotoSearch-Data.cmd；高级命令可直接运行 PhotoSearch.exe。'
            }
        }
    }
    $saved = $null
    if ($Mode -eq 'Configure') {
        try { $saved = Read-SavedRoot } catch { Write-Warning $_.Exception.Message }
    } else {
        $saved = Read-SavedRoot
    }
    if ($Mode -eq 'Configure' -or -not $saved) {
        $selected = Select-NewRoot $saved
    } else {
        $selected = $saved
        if (-not (Test-Path -LiteralPath $selected -PathType Container)) {
            throw "保存的数据目录已不可用：$selected。不会自动在 C 盘建新库。请运行 Set-PhotoSearch-Data.cmd 修改路径。"
        }
    }
    Write-Host "PhotoSearch 数据目录：$selected" -ForegroundColor Cyan
    if ($Mode -eq 'Configure') { exit 0 }
    if ($Mode -eq 'Download') {
        $commandArguments = @('embedding', 'download-qwen', '--confirm-large-download', '--data-root', $selected)
    } else {
        $commandArguments = @('--open-browser', '--data-root', $selected)
        if ($AdditionalArguments) { $commandArguments += $AdditionalArguments }
    }
    & $script:Exe @commandArguments
    exit $LASTEXITCODE
} catch {
    Write-Host ''
    Write-Host ("PhotoSearch 启动失败：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
