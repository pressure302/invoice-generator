$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "app.py"
$python = "python"
$url = $null

Set-Location $root

foreach ($port in 8000..8010) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/state" -TimeoutSec 1 | Out-Null
        $url = "http://127.0.0.1:$port"
        break
    } catch {
    }
}

if (-not $url) {
    Start-Process -FilePath $python -ArgumentList "-B `"$script`"" -WorkingDirectory $root -WindowStyle Hidden

    foreach ($attempt in 1..30) {
        foreach ($port in 8000..8010) {
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/state" -TimeoutSec 1 | Out-Null
                $url = "http://127.0.0.1:$port"
                break
            } catch {
            }
        }
        if ($url) {
            break
        }
        Start-Sleep -Milliseconds 300
    }
}

if (-not $url) {
    throw "Invoice generator did not start."
}

$chromePaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)

$chrome = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList $url
} else {
    Start-Process $url
}
