# Windows PowerShell 5.1; the UTF-8 BOM preserves Chinese prompts.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Start','Download','Configure')]
    [string]$Mode,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$AdditionalArguments
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'PhotoSearch.exe'
if (-not $env:APPDATA) { throw '无法找到 Windows APPDATA 配置目录。' }
$configDir = Join-Path $env:APPDATA 'PhotoSearch'
$configFile = Join-Path $configDir 'portable-data-root.txt'

function Check-Root([string]$InputPath) {
    $path = $InputPath.Trim().Trim('"')
    if ($path -notmatch '^[A-Za-z]:\\') {
        throw '请输入完整的本地磁盘路径，例如 D:\PhotoSearchData（不支持相对路径或网络共享）。'
    }
    $path = [IO.Path]::GetFullPath($path).TrimEnd('\')
    if ($path -match '^[A-Za-z]:$') { throw '请使用磁盘下的专用文件夹，不要直接选择磁盘根目录。' }
    $install = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
    if ($path.Equals($install,[StringComparison]::OrdinalIgnoreCase) -or
        $path.StartsWith($install + '\',[StringComparison]::OrdinalIgnoreCase)) {
        throw '数据目录不能位于程序安装目录内。'
    }
    return $path
}
function Read-Root {
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) { return $null }
    $value = [IO.File]::ReadAllText($configFile,[Text.Encoding]::UTF8)
    if ([string]::IsNullOrWhiteSpace($value)) { throw '数据目录配置为空，请运行 Set-PhotoSearch-Data.cmd。' }
    return (Check-Root $value)
}
function Ask-Root([string]$Previous) {
    Write-Host '请指定 PhotoSearch 数据目录（数据库、模型、索引、缩略图和缓存）。' -ForegroundColor Cyan
    Write-Host '例如：D:\PhotoSearchData 或 E:\PhotoSearchData。不会默认写入 C 盘。'
    if ($Previous) {
        Write-Host "当前：$Previous；更改位置不会迁移或删除旧数据。" -ForegroundColor Yellow
    } elseif ($env:LOCALAPPDATA) {
        $legacy=Join-Path $env:LOCALAPPDATA 'PhotoSearchData'
        if (Test-Path -LiteralPath $legacy -PathType Container) {
            Write-Host "发现旧数据目录 $legacy；不会自动迁移或删除。" -ForegroundColor Yellow
        }
    }
    $answer = Read-Host '输入完整路径（回车取消）'
    if ([string]::IsNullOrWhiteSpace($answer)) { throw '已取消：没有选择数据目录。' }
    $path=Check-Root $answer
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        $ok=Read-Host "目录不存在：$path。输入 Y 创建"
        if ($ok -ne 'Y') { throw '已取消创建目录。' }
        [IO.Directory]::CreateDirectory($path) | Out-Null
    }
    $probe=Join-Path $path ('.photos-search-write-check-' + [Guid]::NewGuid().ToString('N'))
    try { [IO.File]::WriteAllText($probe,'ok') }
    finally { if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force } }
    [IO.Directory]::CreateDirectory($configDir) | Out-Null
    $temporary=Join-Path $configDir ('portable-data-root-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($temporary,$path + [Environment]::NewLine,[Text.Encoding]::UTF8)
        Move-Item -LiteralPath $temporary -Destination $configFile -Force
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
    return $path
}
try {
    if ($Mode -ne 'Configure' -and -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw '找不到 PhotoSearch.exe；请先完整解压 ZIP。'
    }
    if ($Mode -eq 'Start') {
        foreach ($item in @($AdditionalArguments)) {
            if ($item -match '^--data-root(?:=|$)') { throw '请用 Set-PhotoSearch-Data.cmd 更改目录。' }
        }
    }
    $saved=$null
    if ($Mode -eq 'Configure') {
        try { $saved=Read-Root } catch { Write-Warning $_.Exception.Message }
    } else { $saved=Read-Root }
    if ($Mode -eq 'Configure' -or -not $saved) {
        $selected=Ask-Root $saved
    } else {
        $selected=$saved
        if (-not (Test-Path -LiteralPath $selected -PathType Container)) {
            throw "原数据目录不可访问：$selected；不会自动在 C 盘创建新库。请运行 Set-PhotoSearch-Data.cmd。"
        }
    }
    Write-Host "使用数据目录：$selected" -ForegroundColor Cyan
    if ($Mode -eq 'Configure') { exit 0 }
    if ($Mode -eq 'Download') {
        & $exe embedding download-qwen --confirm-large-download --data-root $selected
    } else {
        & $exe --open-browser --data-root $selected @AdditionalArguments
    }
    exit $LASTEXITCODE
} catch {
    Write-Host ("PhotoSearch 操作未完成：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
