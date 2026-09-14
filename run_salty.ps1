# SALTY Backend Windows Runner
# Runs the SALTY Data API (port 8010) and Voice Call Agent (port 8001)

param(
    [int]$ApiPort = 8010,
    [int]$CallAgentPort = 8001
)

$ErrorActionPreference = "Stop"
$RootDir = $PSScriptRoot
$PythonBin = Join-Path $RootDir ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonBin)) {
    Write-Error "Virtual environment not found at $PythonBin. Run setup first."
    exit 1
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Starting SALTY AI Backend Services" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check and stop any lingering processes on specified ports
function Stop-PortProcess([int]$port) {
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            try {
                Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped existing process $($conn.OwningProcess) on port $port" -ForegroundColor Yellow
            } catch {}
        }
    }
}

Stop-PortProcess $ApiPort
Stop-PortProcess $CallAgentPort

$env:SALTY_API_PORT = "$ApiPort"
$env:PORT = "$CallAgentPort"

Write-Host "Starting SALTY Data API on http://127.0.0.1:$ApiPort..." -ForegroundColor Green
$apiProcess = Start-Process -FilePath $PythonBin -ArgumentList "api_server.py" -WorkingDirectory (Join-Path $RootDir "backend") -PassThru

Write-Host "Starting SALTY Voice Call Agent on http://127.0.0.1:$CallAgentPort..." -ForegroundColor Green
$callAgentProcess = Start-Process -FilePath $PythonBin -ArgumentList "-m uvicorn app.main:app --host 0.0.0.0 --port $CallAgentPort" -WorkingDirectory (Join-Path $RootDir "call-agent") -PassThru

# Wait and verify readiness
Start-Sleep -Seconds 3
try {
    $apiHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($apiHealth.ok) {
        Write-Host "SALTY Data API ready: http://127.0.0.1:$ApiPort/api/health" -ForegroundColor Green
    }
} catch {
    Write-Host "Warning: SALTY Data API did not respond immediately." -ForegroundColor Yellow
}

try {
    $agentHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$CallAgentPort/health/live" -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($agentHealth.status -eq "live") {
        Write-Host "SALTY Call Agent ready: http://127.0.0.1:$CallAgentPort/health/live" -ForegroundColor Green
    }
} catch {
    Write-Host "Warning: SALTY Call Agent did not respond immediately." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Services running! Press Ctrl+C to stop both." -ForegroundColor Cyan

try {
    while ($true) {
        if ($apiProcess.HasExited) {
            Write-Host "Data API process exited unexpectedly." -ForegroundColor Red
            break
        }
        if ($callAgentProcess.HasExited) {
            Write-Host "Call Agent process exited unexpectedly." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host "Shutting down services..." -ForegroundColor Yellow
    if ($apiProcess -and -not $apiProcess.HasExited) { Stop-Process -Id $apiProcess.Id -Force }
    Stop-PortProcess $ApiPort
    if ($callAgentProcess -and -not $callAgentProcess.HasExited) { Stop-Process -Id $callAgentProcess.Id -Force }
    Stop-PortProcess $CallAgentPort
    Write-Host "All services stopped." -ForegroundColor Green
}
