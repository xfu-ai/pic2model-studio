param(
    [switch]$Meshy,
    [switch]$Gemini,
    [switch]$Tripo
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not ($Meshy -or $Gemini -or $Tripo)) {
    throw "Select at least one Provider: -Meshy, -Gemini, or -Tripo."
}

$systemPython = (& py -3.14 -c "import sys; print(sys.executable)").Trim()
if (-not $systemPython -or -not (Test-Path -LiteralPath $systemPython)) {
    throw "Official system Python 3.14 is required for real Provider smoke."
}
$opensslVersion = (& $systemPython -c "import ssl; print(ssl.OPENSSL_VERSION)").Trim()
$pythonVersion = (& $systemPython -c "import platform; print(platform.python_version())").Trim()
if ($opensslVersion -match "^OpenSSL 3\.5\.") {
    throw (
        "The selected Python uses $opensslVersion, which is incompatible with " +
        "the configured Meshy/Tripo TLS endpoints. Install official " +
        "Python 3.14 with OpenSSL 3.0.x."
    )
}

$venvRoot = Join-Path $repoRoot ".local\real-provider-venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $systemPython -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the isolated real-Provider environment."
    }
}

$ready = $null
try {
    $ready = & $venvPython -c "import httpx, PIL, pytest; print('ready')" 2>$null
} catch {
    $ready = $null
}
if ($ready -ne "ready") {
    & $venvPython -m pip install --disable-pip-version-check -e $repoRoot pytest
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install real-Provider smoke dependencies."
    }
}

Remove-Item Env:RUN_LIVE_MESHY -ErrorAction SilentlyContinue
Remove-Item Env:RUN_LIVE_GEMINI -ErrorAction SilentlyContinue
Remove-Item Env:RUN_LIVE_TRIPO -ErrorAction SilentlyContinue
$env:ALLOW_REAL_PROVIDER_SMOKE = "1"

$tests = [System.Collections.Generic.List[string]]::new()
if ($Meshy) {
    $env:RUN_LIVE_MESHY = "1"
    $tests.Add(
        "tests/smoke/real_providers/test_meshy.py::test_meshy_minimal_t2i_returns_one_valid_image"
    )
}
if ($Gemini) {
    $env:RUN_LIVE_GEMINI = "1"
    $tests.Add(
        "tests/smoke/real_providers/test_gemini.py::test_gemini_minimal_content_analysis_returns_bilingual_result"
    )
}
if ($Tripo) {
    $env:RUN_LIVE_TRIPO = "1"
    $tests.Add(
        "tests/smoke/real_providers/test_tripo.py::test_tripo_minimal_single_image_lifecycle"
    )
}

$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$evidence = Join-Path $repoRoot "tests\evidence\real-provider\$stamp"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$log = Join-Path $evidence "pytest.log"

Push-Location $repoRoot
try {
    & $venvPython -m pytest @tests -q *>&1 |
        ForEach-Object {
            "$_" `
                -replace [regex]::Escape($repoRoot), "<WORKSPACE>" `
                -replace [regex]::Escape($env:USERPROFILE), "<USER_HOME>" `
                -replace [regex]::Escape($env:TEMP), "<TEMP>"
        } |
        Tee-Object -FilePath $log
    $exitCode = $LASTEXITCODE
    @{
        python_version = $pythonVersion
        openssl = $opensslVersion
        providers = @(
            if ($Meshy) { "meshy" }
            if ($Gemini) { "gemini" }
            if ($Tripo) { "tripo" }
        )
        exit_code = $exitCode
    } | ConvertTo-Json | Set-Content (Join-Path $evidence "summary.json")
    exit $exitCode
}
finally {
    Pop-Location
}
