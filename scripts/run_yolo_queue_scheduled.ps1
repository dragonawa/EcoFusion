$ErrorActionPreference = "Stop"

$worker = "C:\ekolog\scripts\run_yolo_queue_worker.ps1"
$logDir = "C:\ekolog\data\yolo\worker_logs"
$log = Join-Path $logDir "worker.log"

New-Item -ItemType Directory -Path $logDir `
    -Force | Out-Null

$mutex = [System.Threading.Mutex]::new(
    $false,
    "Local\EcoFusionYOLOWorker"
)

$locked = $false
$transcriptStarted = $false

try {
    try {
        $locked = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $locked = $true
    }

    if (-not $locked) {
        Write-Host "Another YOLO worker is already running"
        return
    }

    if ((Test-Path $log) -and
        (Get-Item $log).Length -gt 5MB) {
        Move-Item $log (
            Join-Path $logDir "worker_previous.log"
        ) -Force
    }

    Start-Transcript -Path $log -Append | Out-Null
    $transcriptStarted = $true

    Write-Host "=== ECOFUSION AUTO YOLO WORKER ==="
    Write-Host "Started: $(Get-Date -Format o)"

    & $worker -RunOnce

    Write-Host "Finished: $(Get-Date -Format o)"
}
catch {
    Write-Host "WORKER FAILED: $($_.Exception.Message)"
    throw
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }

    if ($locked) {
        $mutex.ReleaseMutex()
    }

    $mutex.Dispose()
}
