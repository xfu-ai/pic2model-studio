param(
    [int]$DebugPort = 9226,
    [int]$DevPort = 14201,
    [string]$EvidenceRoot = (Join-Path $PSScriptRoot "..\tests\evidence\controlled-webview2"),
    [switch]$CreateProject,
    [switch]$ImageCanvas,
    [switch]$MockTripoApproval,
    [switch]$RecoverOffline,
    [switch]$KeepApp,
    [switch]$NoCdp
)

$ErrorActionPreference = "Stop"
if ($NoCdp -and -not $KeepApp) { throw "-NoCdp requires -KeepApp so the interactive test window remains available." }
if ($ImageCanvas -and -not $CreateProject) { throw "-ImageCanvas requires -CreateProject in an isolated run." }
if ($MockTripoApproval -and -not $CreateProject) { throw "-MockTripoApproval requires -CreateProject in an isolated run." }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$runRoot = Join-Path $EvidenceRoot $stamp
$fixtures = Join-Path $runRoot "fixtures"
$appData = Join-Path $runRoot "app-data"
# Tauri's config accepts only a relative WebView data directory. An absolute
# path is deliberately ignored by Tauri, which would make multiple test hosts
# share the default WebView2 environment and lose their distinct debug flags.
$webviewDataDirectory = "aipic-controlled-e2e-$stamp"
# Keep compiler artifacts separate from app data and fixtures, but reuse them
# across runs. Tauri rebuilds when TAURI_CONFIG changes.
$target = Join-Path $repoRoot ".local\controlled-webview2-target-$DebugPort"
$config = Join-Path $runRoot "tauri.controlled.json"
$viteLog = Join-Path $runRoot "vite.log"
$tauriLog = Join-Path $runRoot "tauri.log"
$tauriError = Join-Path $runRoot "tauri.err.log"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

function Wait-Http([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null
            return
        } catch { Start-Sleep -Milliseconds 250 }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Url"
}

function Stop-ControlledWebViewHosts {
    $controlledRoots = @(
        (Join-Path $repoRoot ".local\controlled-webview2-target-"),
        (Join-Path $repoRoot ".local\webview2-core-e2e-target-")
    )
    foreach ($process in @(Get-Process -Name "pic2model-studio" -ErrorAction SilentlyContinue)) {
        $path = $process.Path
        if ($null -ne $path -and ($controlledRoots | Where-Object { $path.StartsWith($_, [System.StringComparison]::OrdinalIgnoreCase) })) {
            # Kill the known controlled host and its sidecar descendants as a
            # unit. The pnpm/cargo parents then exit naturally, while the
            # developer-owned Vite server is intentionally left running.
            # Run through cmd so a Windows resource-pressure diagnostic from
            # taskkill does not abort the launcher before the direct fallback.
            & cmd.exe /d /c "taskkill.exe /PID $($process.Id) /T /F >nul 2>&1"
            if ($LASTEXITCODE -ne 0) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Start-Sleep -Milliseconds 500
}

function Stop-ProcessTree([System.Diagnostics.Process]$Process) {
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) {
            # `pnpm tauri dev` owns cargo, the desktop host and its sidecar.
            # Stopping only the wrapper leaves those descendants alive, which
            # can retain WebView2 browser processes and eventually makes a
            # subsequent controlled run fail to create its page.
            & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
        }
    } catch { }
}

$vite = $null
$tauri = $null
Push-Location $repoRoot
try {
    # A WebView2 environment locks its browser flags for the lifetime of its
    # user-data directory. Never allow a previous controlled host to leak into
    # a fresh CDP run; normal/user-owned desktop binaries do not match here.
    Stop-ControlledWebViewHosts
    & .\.venv\Scripts\python.exe scripts\create_controlled_e2e_fixtures.py --output $fixtures |
        Set-Content (Join-Path $runRoot "fixtures.json")

    $configuration = @{
        app = @{
            windows = @(@{
                label = "main"
                title = "Pic2Model Studio controlled E2E"
                width = 1280
                height = 800
                minWidth = 1024
                minHeight = 640
                dataDirectory = $webviewDataDirectory
                additionalBrowserArgs = "--remote-debugging-port=$DebugPort --remote-allow-origins=http://127.0.0.1:$DebugPort"
            })
        }
    }
    if ($DevPort -ne 14200) {
        $configuration.build = @{
            devUrl = "http://127.0.0.1:$DevPort"
            beforeDevCommand = "powershell -NoProfile -Command exit 0"
        }
    } else {
        # Preserve the base devUrl while avoiding a second Vite instance when
        # the developer server is already listening on the default port.
        $configuration.build = @{ beforeDevCommand = "powershell -NoProfile -Command exit 0" }
    }
    $configuration | ConvertTo-Json -Depth 8 | Set-Content $config

    $alreadyServing = $false
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$DevPort" -TimeoutSec 2 | Out-Null
        $alreadyServing = $true
    } catch { }
    if (-not $alreadyServing) {
        $vite = Start-Process -FilePath "pnpm.cmd" -ArgumentList @(
            "--dir", "frontend", "dev", "--host", "127.0.0.1", "--port", $DevPort, "--strictPort"
        ) -WorkingDirectory (Join-Path $repoRoot "desktop") -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $viteLog -RedirectStandardError (Join-Path $runRoot "vite.err.log")
    }
    Wait-Http "http://127.0.0.1:$DevPort" 30

    $env:AIPIC_CONTROLLED_E2E = "1"
    $env:AIPIC_CONTROLLED_E2E_FIXTURE_ROOT = $fixtures
    $env:AIPIC_CONTROLLED_E2E_APP_DATA = $appData
    $env:AIPIC_CONTROLLED_E2E_RENDERER_ORIGIN = "http://127.0.0.1:$DevPort"
    $env:AIPIC_TO_MODEL_PYTHON = (Join-Path $repoRoot ".venv\Scripts\python.exe")
    Remove-Item Env:AIPIC_CONTROLLED_E2E_HEALTH_FAILURES -ErrorAction SilentlyContinue
    if ($RecoverOffline) {
        # React Strict Mode may initiate the guarded startup effect twice in a
        # Debug WebView. Fail both initial probes, then let the user's one
        # reconnect action consume the first healthy response.
        $env:AIPIC_CONTROLLED_E2E_HEALTH_FAILURES = "2"
    }
    $env:CARGO_TARGET_DIR = $target
    $tauriLaunch = @{
        FilePath = "pnpm.cmd"
        ArgumentList = @("tauri", "dev", "--no-watch", "--config", $config)
        WorkingDirectory = (Join-Path $repoRoot "desktop")
        PassThru = $true
        RedirectStandardOutput = $tauriLog
        RedirectStandardError = $tauriError
    }
    if (-not $NoCdp) { $tauriLaunch.WindowStyle = "Hidden" }
    $tauri = Start-Process @tauriLaunch
    if ($NoCdp) { return }
    Wait-Http "http://127.0.0.1:$DebugPort/json/list" 180

    $arguments = @(
        "scripts\run_controlled_webview2_e2e.py", "--debug-port", $DebugPort,
        "--output", (Join-Path $runRoot "webview-evidence")
    )
    if (-not $KeepApp) { $arguments += "--close-window" }
    if ($CreateProject) { $arguments += "--create-project", "--import-image" }
    if ($ImageCanvas) { $arguments += "--image-canvas" }
    if ($MockTripoApproval) { $arguments += "--mock-tripo-approval" }
    if ($RecoverOffline) { $arguments += "--recover-offline" }
    & .\.venv\Scripts\python.exe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Controlled WebView2 DOM runner failed with exit code $LASTEXITCODE. Evidence: $runRoot"
    }
} finally {
    if (-not $KeepApp) {
        Stop-ProcessTree $tauri
        Stop-ProcessTree $vite
        Stop-ControlledWebViewHosts
    }
    Pop-Location
}
