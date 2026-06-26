$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OutputRoot = Join-Path $ProjectRoot "build\nuitka_sidecar_external_node_safe_probe"
$BuildRoot = Join-Path $ProjectRoot "build"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python do ambiente virtual nao encontrado em: $Python"
}

Push-Location $ProjectRoot
try {
    New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
    $ResolvedBuildRoot = (Resolve-Path $BuildRoot).Path.TrimEnd("\") + "\"
    $ResolvedOutputRoot = (Resolve-Path $OutputRoot).Path.TrimEnd("\") + "\"
    $RunningFromOutput = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($ResolvedOutputRoot)
    }

    if ($RunningFromOutput) {
        $ProcessList = ($RunningFromOutput | ForEach-Object {
            "$($_.ProcessName) pid=$($_.Id) path=$($_.Path)"
        }) -join [Environment]::NewLine

        throw "Pare o sidecar antigo antes de buildar. Processos usando a pasta de build:$([Environment]::NewLine)$ProcessList"
    }

    foreach ($Target in @(
        (Join-Path $OutputRoot "run_omnipublisher.dist"),
        (Join-Path $OutputRoot "run_omnipublisher.build")
    )) {
        if (Test-Path -LiteralPath $Target) {
            $ResolvedTarget = Resolve-Path -LiteralPath $Target
            if (-not $ResolvedTarget.Path.StartsWith($ResolvedBuildRoot)) {
                throw "Caminho de build recusado para limpeza: $ResolvedTarget"
            }
            Remove-Item -LiteralPath $ResolvedTarget.Path -Recurse -Force
        }
    }

    & $Python -m nuitka run_omnipublisher.py

    $DistRoot = Join-Path $OutputRoot "run_omnipublisher.dist"
    $TikTokConfigSource = Join-Path $ProjectRoot ".venv\Lib\site-packages\tiktok_uploader\config.toml"
    $TikTokConfigTarget = Join-Path $DistRoot "tiktok_uploader\config.toml"
    if (-not (Test-Path -LiteralPath $TikTokConfigSource)) {
        throw "config.toml do tiktok_uploader nao encontrado em: $TikTokConfigSource"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $TikTokConfigTarget) | Out-Null
    Copy-Item -LiteralPath $TikTokConfigSource -Destination $TikTokConfigTarget -Force

    $PlaywrightNode = Join-Path $DistRoot "playwright\driver\node.exe"
    if (Test-Path -LiteralPath $PlaywrightNode) {
        $ResolvedNode = Resolve-Path -LiteralPath $PlaywrightNode
        $ResolvedDistRoot = (Resolve-Path $DistRoot).Path.TrimEnd("\") + "\"
        if (-not $ResolvedNode.Path.StartsWith($ResolvedDistRoot)) {
            throw "Caminho de node.exe recusado para limpeza: $ResolvedNode"
        }
        Remove-Item -LiteralPath $ResolvedNode.Path -Force
    }

    foreach ($OptionalPilBinary in @(
        "PIL\_avif.pyd",
        "PIL\_webp.pyd",
        "PIL\_imagingcms.pyd"
    )) {
        $PilTarget = Join-Path $DistRoot $OptionalPilBinary
        if (Test-Path -LiteralPath $PilTarget) {
            $ResolvedPilTarget = Resolve-Path -LiteralPath $PilTarget
            $ResolvedDistRoot = (Resolve-Path $DistRoot).Path.TrimEnd("\") + "\"
            if (-not $ResolvedPilTarget.Path.StartsWith($ResolvedDistRoot)) {
                throw "Caminho de binario PIL recusado para limpeza: $ResolvedPilTarget"
            }
            Remove-Item -LiteralPath $ResolvedPilTarget.Path -Force
        }
    }
}
finally {
    Pop-Location
}
