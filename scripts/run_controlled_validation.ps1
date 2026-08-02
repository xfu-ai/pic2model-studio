param(
    [string]$EvidenceRoot = (Join-Path $PSScriptRoot "..\tests\evidence\controlled-validation"),
    [switch]$KeepGoing,
    [int]$WebViewDebugPort = 0,
    [switch]$CreateWebViewProject
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$evidence = Join-Path $EvidenceRoot $stamp
New-Item -ItemType Directory -Force -Path $evidence | Out-Null

# Paid services are disabled even if the caller has credentials in their profile.
Remove-Item Env:RUN_LIVE_TRIPO -ErrorAction SilentlyContinue
Remove-Item Env:RUN_LIVE_GEMINI -ErrorAction SilentlyContinue
Remove-Item Env:RUN_LIVE_OPENAI -ErrorAction SilentlyContinue
Remove-Item Env:RUN_LIVE_NANOBANANA -ErrorAction SilentlyContinue
Remove-Item Env:RUN_LIVE_LLM_TESTS -ErrorAction SilentlyContinue
Remove-Item Env:ALLOW_REAL_PROVIDER_SMOKE -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_CONTROLLED_E2E -ErrorAction SilentlyContinue
Remove-Item Env:AIPIC_CONTROLLED_E2E_PROVIDER_FAILURE -ErrorAction SilentlyContinue

$results = [System.Collections.Generic.List[object]]::new()
function Invoke-ValidationStep([string]$Name, [scriptblock]$Command) {
    $log = Join-Path $evidence "$Name.log"
    # pnpm writes progress text to stderr even on success. Keep that output in
    # the evidence log without allowing PowerShell's native-command preference
    # to turn it into a terminating error before we inspect the exit code.
    $previousErrorActionPreference = $ErrorActionPreference
    $previousNativePreference = $PSNativeCommandUseErrorActionPreference
    try {
        # Native tools such as pnpm write harmless progress messages to stderr.
        # Their authoritative status is $LASTEXITCODE below; suppress the
        # duplicate PowerShell error records while retaining normal stdout in
        # the per-step log.
        $ErrorActionPreference = "SilentlyContinue"
        $PSNativeCommandUseErrorActionPreference = $false
        & $Command *>&1 | Tee-Object -FilePath $log
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
    $results.Add([ordered]@{ name = $Name; exit_code = $code; log = $log })
    if ($code -ne 0 -and -not $KeepGoing) { throw "$Name failed with exit code $code" }
}

Push-Location $repoRoot
try {
    Get-ChildItem Env:RUN_LIVE_*, Env:ALLOW_REAL_PROVIDER_SMOKE -ErrorAction SilentlyContinue |
        Select-Object Name, Value | ConvertTo-Json | Set-Content (Join-Path $evidence "live-flags.json")
    git status --short | Set-Content (Join-Path $evidence "git-status.txt")

    $webViewFixtures = Join-Path $evidence "webview2-fixtures"
    Invoke-ValidationStep "controlled-fixtures" { & .\.venv\Scripts\python.exe scripts\create_controlled_e2e_fixtures.py --output $webViewFixtures }
    $e2eTemp = Join-Path $evidence "pytest-e2e-security"
    $contractTemp = Join-Path $evidence "pytest-contract"
    $integrationTemp = Join-Path $evidence "pytest-integration"
    Invoke-ValidationStep "python-e2e-security" { & .\.venv\Scripts\python.exe -m pytest tests\e2e tests\security -q --disable-warnings --maxfail=1 --basetemp $e2eTemp }
    Invoke-ValidationStep "python-contract" { & .\.venv\Scripts\python.exe -m pytest tests\contract -q --disable-warnings --maxfail=1 --basetemp $contractTemp }
    Invoke-ValidationStep "python-integration" { & .\.venv\Scripts\python.exe -m pytest tests\integration -q --disable-warnings --maxfail=1 --basetemp $integrationTemp }
    Invoke-ValidationStep "frontend-dom" { Push-Location desktop\frontend; try { pnpm test 2>&1 } finally { Pop-Location } }
    Invoke-ValidationStep "frontend-build" { Push-Location desktop\frontend; try { pnpm build 2>&1 } finally { Pop-Location } }
    Invoke-ValidationStep "tauri-host" { cargo test --manifest-path desktop\src-tauri\Cargo.toml 2>&1 }
    if ($WebViewDebugPort -gt 0) {
        $webViewEvidence = Join-Path $evidence "webview2-dom"
        $webViewArguments = @("scripts\run_controlled_webview2_e2e.py", "--debug-port", $WebViewDebugPort, "--output", $webViewEvidence)
        if ($CreateWebViewProject) { $webViewArguments += "--create-project", "--import-image" }
        Invoke-ValidationStep "webview2-dom" { & .\.venv\Scripts\python.exe @webViewArguments }
    }
} catch {
    $_ | Out-String | Set-Content (Join-Path $evidence "failure.txt")
    if (-not $KeepGoing) { throw }
} finally {
    $results | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $evidence "summary.json")
    Pop-Location
}
