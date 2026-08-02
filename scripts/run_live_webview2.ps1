param(
    [int]$DebugPort = 9250,
    [int]$DevPort = 14200,
    [string]$SessionRoot = (Join-Path $PSScriptRoot "..\.local\live-webview2")
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sessionRoot = [System.IO.Path]::GetFullPath($SessionRoot)
$workspaceLocal = [System.IO.Path]::GetFullPath((Join-Path $repoRoot ".local"))
if (-not $sessionRoot.StartsWith($workspaceLocal, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The live session directory must stay under the workspace .local directory."
}

$runRoot = Join-Path $sessionRoot "desktop-$DebugPort"
$target = Join-Path $repoRoot ".local\live-webview2-target-$DebugPort"
$config = Join-Path $runRoot "tauri.live.json"
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
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Url"
}

try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$DebugPort/json/list" -TimeoutSec 2 |
        Out-Null
    @{
        status = "already_running"
        debug_port = $DebugPort
        dev_port = $DevPort
        session_root = $runRoot
    } | ConvertTo-Json
    exit 0
} catch {
    # A missing CDP endpoint means this launcher may start its own live host.
}

$configuration = @{
    build = @{
        devUrl = "http://127.0.0.1:$DevPort"
        beforeDevCommand = "powershell -NoProfile -Command exit 0"
    }
    app = @{
        windows = @(@{
            label = "main"
            title = "AIPicToModel Live HMR"
            width = 1440
            height = 900
            minWidth = 1024
            minHeight = 640
            dataDirectory = "aipic-live-webview2-$DebugPort"
            additionalBrowserArgs = "--remote-debugging-port=$DebugPort --remote-allow-origins=http://127.0.0.1:$DebugPort"
        })
    }
}
$configuration | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $config

$vite = $null
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$DevPort" -TimeoutSec 2 | Out-Null
} catch {
    $vite = Start-Process -FilePath "pnpm.cmd" -ArgumentList @(
        "--dir", "frontend", "dev", "--host", "127.0.0.1",
        "--port", $DevPort, "--strictPort"
    ) -WorkingDirectory (Join-Path $repoRoot "desktop") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $viteLog `
        -RedirectStandardError (Join-Path $runRoot "vite.err.log")
}
Wait-Http "http://127.0.0.1:$DevPort" 30

# This is the real application path. Explicitly clear every controlled Provider
# switch before launching the child process while preserving the user's normal
# app-data, keyring profiles and paid-action approval flow.
Remove-Item Env:AIPIC_CONTROLLED_E2E -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_CONTROLLED_E2E_APP_DATA -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_CONTROLLED_E2E_FIXTURE_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_CONTROLLED_E2E_PROVIDER_FAILURE -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_CONTROLLED_E2E_RENDERER_ORIGIN -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_TO_MODEL_PYTHON -ErrorAction SilentlyContinue
$env:AIPIC_TO_MODEL_FORCE_PYTHON = "1"
$env:AIPIC_TO_MODEL_STARTUP_DIAGNOSTICS = "1"
$env:CARGO_TARGET_DIR = $target

$tauri = Start-Process -FilePath "pnpm.cmd" -ArgumentList @(
    "tauri", "dev", "--no-watch", "--config", $config
) -WorkingDirectory (Join-Path $repoRoot "desktop") -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $tauriLog -RedirectStandardError $tauriError

Wait-Http "http://127.0.0.1:$DebugPort/json/list" 180
@{
    status = "started"
    debug_port = $DebugPort
    dev_port = $DevPort
    tauri_process_id = $tauri.Id
    vite_process_id = if ($vite) { $vite.Id } else { $null }
    reused_vite = $null -eq $vite
    session_root = $runRoot
} | ConvertTo-Json | Tee-Object -FilePath (Join-Path $runRoot "session.json")
