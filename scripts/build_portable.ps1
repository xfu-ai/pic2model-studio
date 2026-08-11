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
$localModelsDirectory = Join-Path $repositoryRoot "resources\local-models"
$ollamaDirectory = Join-Path $localModelsDirectory "ollama\runtime"
$ollamaModelsDirectory = Join-Path $localModelsDirectory "ollama\models"
$zImageDirectory = Join-Path $localModelsDirectory "z-image-turbo"
$triposrDirectory = Join-Path $localModelsDirectory "triposr"

$requiredFiles = @(
    $releaseExecutable,
    $sidecarExecutable,
    (Join-Path $ollamaDirectory "ollama.exe"),
    (Join-Path $zImageDirectory "runtime\sd-cli.exe"),
    (Join-Path $zImageDirectory "models\z_image_turbo-Q3_K.gguf"),
    (Join-Path $zImageDirectory "models\ae.safetensors"),
    (Join-Path $zImageDirectory "models\Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
    (Join-Path $triposrDirectory "python\python.exe"),
    (Join-Path $triposrDirectory "source\run.py"),
    (Join-Path $triposrDirectory "model\config.yaml"),
    (Join-Path $triposrDirectory "model\model.ckpt")
)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required portable artifact is missing: $requiredFile"
    }
}
foreach ($requiredDirectory in @(
    $ollamaDirectory,
    $ollamaModelsDirectory,
    $zImageDirectory,
    $triposrDirectory
)) {
    if (-not (Test-Path -LiteralPath $requiredDirectory -PathType Container)) {
        throw "Required local Qwen runtime directory is missing: $requiredDirectory"
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

# A portable release is distributed as this directory alone.  Keep the user
# instructions, dependency inventory, and third-party notices with the binary
# instead of requiring a recipient to retain the source checkout beside it.
$releaseDocuments = @{
    (Join-Path $repositoryRoot "portable\README.md") = "README.md"
    (Join-Path $repositoryRoot "DEPENDENCIES.md") = "DEPENDENCIES.md"
    (Join-Path $repositoryRoot "THIRD_PARTY_NOTICES.md") = "THIRD_PARTY_NOTICES.md"
}
foreach ($entry in $releaseDocuments.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Key -PathType Leaf)) {
        throw "Required portable release document is missing: $($entry.Key)"
    }
    Copy-Item -LiteralPath $entry.Key -Destination (Join-Path $portableRoot $entry.Value)
}

$portableOllama = Join-Path $portableRoot "resources\ollama"
$portableOllamaModels = Join-Path $portableRoot "resources\ollama-models"
Copy-Item -LiteralPath $ollamaDirectory -Destination $portableOllama -Recurse
Copy-Item -LiteralPath $ollamaModelsDirectory -Destination $portableOllamaModels -Recurse
$portableLocalModels = Join-Path $portableRoot "resources\local-models"
New-Item -ItemType Directory -Path $portableLocalModels -Force | Out-Null
Copy-Item -LiteralPath $zImageDirectory -Destination $portableLocalModels -Recurse
Copy-Item -LiteralPath $triposrDirectory -Destination $portableLocalModels -Recurse

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
    "resources/ollama contains the pinned Ollama v0.32.5 Windows runtime.",
    "resources/ollama-models contains the verified local Qwen3-VL model store.",
    "resources/local-models/z-image-turbo contains the pinned stable-diffusion.cpp runtime and Z-Image-Turbo weights.",
    "resources/local-models/triposr contains portable CPython 3.11, the pinned TripoSR source and offline model store.",
    "README.md, DEPENDENCIES.md, and THIRD_PARTY_NOTICES.md are release documentation included with this directory.",
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
