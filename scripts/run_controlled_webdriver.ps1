param(
    [int]$DriverPort = 4667,
    [int]$NativeDriverPort = 4669,
    [int]$DevPort = 14200,
    [string]$BinaryPath = "",
    [string]$WebviewUserDataFolder = "",
    [string]$EvidenceRoot = (Join-Path $PSScriptRoot "..\tests\evidence\controlled-webdriver"),
    [int]$SessionTimeout = 120,
    [switch]$CreateProject,
    [switch]$TargetExtraction,
    [switch]$RecoverOffline,
    [switch]$TauriDriver,
    [string]$TauriDriverManifest = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$runRoot = Join-Path $EvidenceRoot $stamp
$fixtures = Join-Path $runRoot "fixtures"
$appData = Join-Path $runRoot "app-data"
$driverPath = Join-Path $repoRoot ".local\msedgedriver\150.0.4078.99\msedgedriver.exe"
$cargoRegistry = Join-Path $env:USERPROFILE ".cargo\registry\src"
$tauriDriverManifestPath = if ($TauriDriverManifest) {
    (Resolve-Path $TauriDriverManifest).Path
} elseif (Test-Path -LiteralPath $cargoRegistry) {
    Get-ChildItem -LiteralPath $cargoRegistry -Recurse -Filter Cargo.toml -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -like "tauri-driver-*" } |
        Select-Object -First 1 -ExpandProperty FullName
} else {
    $null
}
$binary = if ($BinaryPath) { (Resolve-Path $BinaryPath).Path } else { Join-Path $repoRoot "desktop\src-tauri\target\debug\pic2model-studio.exe" }
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

function Wait-Http([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try { Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null; return }
        catch { Start-Sleep -Milliseconds 250 }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Url"
}

if (-not (Test-Path -LiteralPath $driverPath)) { throw "Edge WebDriver was not found at $driverPath" }
if (-not (Test-Path -LiteralPath $binary)) { throw "The Tauri binary was not found at $binary" }
if ($TauriDriver -and (-not $tauriDriverManifestPath -or -not (Test-Path -LiteralPath $tauriDriverManifestPath))) {
    throw "A Tauri WebDriver manifest was not found. Pass -TauriDriverManifest explicitly."
}
Push-Location $repoRoot
$vite = $null
$driver = $null
try {
    & .\.venv\Scripts\python.exe scripts\create_controlled_e2e_fixtures.py --output $fixtures |
        Set-Content (Join-Path $runRoot "fixtures.json")
    & cargo build --manifest-path desktop\src-tauri\Cargo.toml | Set-Content (Join-Path $runRoot "cargo-build.log")

    $vite = Start-Process -FilePath "pnpm.cmd" -ArgumentList @("--dir", "frontend", "dev", "--host", "127.0.0.1", "--port", $DevPort, "--strictPort") `
        -WorkingDirectory (Join-Path $repoRoot "desktop") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runRoot "vite.log") -RedirectStandardError (Join-Path $runRoot "vite.err.log")
    Start-Sleep -Milliseconds 500
    if ($vite.HasExited) {
        throw "The isolated Vite server exited early. See $(Join-Path $runRoot 'vite.err.log')."
    }
    Wait-Http "http://127.0.0.1:$DevPort" 30

    $env:AIPIC_CONTROLLED_E2E = "1"
    $env:AIPIC_CONTROLLED_E2E_FIXTURE_ROOT = $fixtures
    $env:AIPIC_CONTROLLED_E2E_APP_DATA = $appData
    $env:AIPIC_CONTROLLED_E2E_RENDERER_ORIGIN = "http://127.0.0.1:$DevPort"
    $env:AIPIC_TO_MODEL_PYTHON = (Join-Path $repoRoot ".venv\Scripts\python.exe")
    Remove-Item Env:AIPIC_CONTROLLED_E2E_HEALTH_FAILURES -ErrorAction SilentlyContinue
    if ($RecoverOffline) { $env:AIPIC_CONTROLLED_E2E_HEALTH_FAILURES = "1" }
    if ($TauriDriver) {
        $driver = Start-Process -FilePath "cargo.exe" -ArgumentList @("run", "--quiet", "--manifest-path", $tauriDriverManifestPath, "--", "--port=$DriverPort", "--native-port=$NativeDriverPort", "--native-driver", $driverPath) -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runRoot "driver.log") -RedirectStandardError (Join-Path $runRoot "driver.err.log")
    } else {
        $driver = Start-Process -FilePath $driverPath -ArgumentList "--port=$DriverPort" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runRoot "driver.log") -RedirectStandardError (Join-Path $runRoot "driver.err.log")
    }
    Wait-Http "http://127.0.0.1:$DriverPort/status" 30

    $arguments = @("scripts\run_controlled_webdriver_e2e.py", "--driver-url", "http://127.0.0.1:$DriverPort", "--binary", $binary, "--output", (Join-Path $runRoot "webview-evidence"), "--session-timeout", $SessionTimeout)
    if ($WebviewUserDataFolder) { $arguments += "--webview-user-data-folder", (Resolve-Path $WebviewUserDataFolder).Path }
    if ($CreateProject) { $arguments += "--create-project", "--import-image" }
    if ($TargetExtraction) { $arguments += "--target-extraction" }
    if ($RecoverOffline) { $arguments += "--recover-offline" }
    if ($TauriDriver) { $arguments += "--tauri-driver" }
    & .\.venv\Scripts\python.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Controlled WebDriver DOM checks failed." }
} finally {
    foreach ($process in @($driver, $vite)) {
        if ($null -ne $process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
    Pop-Location
}
