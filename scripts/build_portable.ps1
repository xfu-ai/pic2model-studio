[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$portableRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $repositoryRoot "portable\Pic2Model-Studio")
)
$expectedPortableParent = [System.IO.Path]::GetFullPath(
    (Join-Path $repositoryRoot "portable")
) + [System.IO.Path]::DirectorySeparatorChar

if (-not $portableRoot.StartsWith(
    $expectedPortableParent,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to replace a portable directory outside this repository."
}

if (-not $SkipBuild) {
    Push-Location $repositoryRoot
    try {
        & pnpm --dir desktop exec tauri build --no-bundle
        if ($LASTEXITCODE -ne 0) {
            throw "The portable Tauri build failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

$releaseExecutable = Join-Path $repositoryRoot "desktop\src-tauri\target\release\pic2model-studio.exe"
$sidecarDirectory = Join-Path $repositoryRoot "desktop\src-tauri\resources\sidecar"
$sidecarExecutable = Join-Path $sidecarDirectory "pic2model-sidecar.exe"

foreach ($requiredFile in @($releaseExecutable, $sidecarExecutable)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required portable artifact is missing: $requiredFile"
    }
}

if (Test-Path -LiteralPath $portableRoot) {
    $resolvedPortableRoot = (Resolve-Path -LiteralPath $portableRoot).Path
    if (-not $resolvedPortableRoot.StartsWith(
        $expectedPortableParent,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to replace an unexpected portable directory."
    }
    Remove-Item -LiteralPath $resolvedPortableRoot -Recurse -Force
}

$portableSidecar = Join-Path $portableRoot "resources\sidecar"
New-Item -ItemType Directory -Path $portableSidecar -Force | Out-Null
Copy-Item -LiteralPath $releaseExecutable -Destination $portableRoot
Copy-Item -LiteralPath $sidecarExecutable -Destination $portableSidecar

$sidecarReadme = Join-Path $sidecarDirectory "README.txt"
if (Test-Path -LiteralPath $sidecarReadme -PathType Leaf) {
    Copy-Item -LiteralPath $sidecarReadme -Destination $portableSidecar
}

$archiveViewer = Join-Path $repositoryRoot ".venv\Scripts\pyi-archive_viewer.exe"
if (-not (Test-Path -LiteralPath $archiveViewer -PathType Leaf)) {
    throw "PyInstaller archive viewer is missing: $archiveViewer"
}
$archiveListing = & $archiveViewer -l $sidecarExecutable
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the packaged sidecar archive."
}
$normalizedArchiveListing = $archiveListing -replace '\\\\', '\'
$requiredArchiveEntries = @(
    "aipic_to_model\resources\image_processing\models\realesrgan-x4.onnx",
    "onnxruntime\capi\onnxruntime.dll",
    "onnxruntime\capi\onnxruntime_providers_shared.dll",
    "onnxruntime\capi\onnxruntime_pybind11_state.pyd",
    "python314.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll"
)
foreach ($archiveEntry in $requiredArchiveEntries) {
    if (-not ($normalizedArchiveListing -like "*$archiveEntry*")) {
        throw "The packaged sidecar is missing a required runtime component: $archiveEntry"
    }
}

$componentManifest = Join-Path $portableRoot "BUNDLED_COMPONENTS.txt"
$componentLines = @(
    "Pic2Model Studio portable runtime components",
    "",
    "resources/sidecar/pic2model-sidecar.exe is a PyInstaller one-file archive.",
    "It embeds these required model and native runtime components:",
    ""
) + ($requiredArchiveEntries | ForEach-Object { "- $($_.Replace('\', '/'))" }) + @(
    "",
    "The one-file sidecar extracts its embedded runtime to a temporary directory at launch.",
    "Python, ONNX Runtime, and Visual C++ runtime DLLs do not need to be installed separately."
)
[System.IO.File]::WriteAllLines(
    $componentManifest,
    $componentLines,
    [System.Text.UTF8Encoding]::new($false)
)

$manifestPath = Join-Path $portableRoot "SHA256SUMS.txt"
$manifestLines = Get-ChildItem -LiteralPath $portableRoot -Recurse -File |
    Where-Object { $_.FullName -ne $manifestPath } |
    Sort-Object FullName |
    ForEach-Object {
        $relativePath = $_.FullName.Substring($portableRoot.Length + 1).Replace("\", "/")
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relativePath"
    }
[System.IO.File]::WriteAllLines(
    $manifestPath,
    $manifestLines,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Output "Portable application created at $portableRoot"
Get-ChildItem -LiteralPath $portableRoot -Recurse -File |
    Select-Object Length, FullName
