param(
    [Parameter(Mandatory=$true)]
    [long]$SearchRunId
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://localhost:8787"

if ($SearchRunId -le 0) {
    throw "SearchRunId must be positive"
}

Write-Host "=== ECOFUSION: MANUAL YOLO11N ANALYSIS ==="
Write-Host "Search run: $SearchRunId"

# Identify the publication and existing results.
$detail = Invoke-RestMethod `
    -Uri "$baseUrl/api/yolo-run-detections/$SearchRunId" `
    -TimeoutSec 20

$sid = [long]$detail.silver_id
$expected = 4 * [int]$detail.selected_count

if ($expected -le 0 -or $expected -gt 20) {
    throw "Invalid panorama candidate count"
}

Write-Host "Publication: $sid"
Write-Host "Expected fragments: $expected"

if ($detail.fragment_count -eq $expected) {
    Write-Host "Analysis already completed"
    Write-Host "Detections: $($detail.total_detections)"
    Write-Host "Database: unchanged"
    Write-Host "MANUAL ANALYSIS: ALREADY COMPLETED"
    return
}

if ($detail.fragment_count -ne 0) {
    throw "Partial results exist; manual inspection required"
}

Write-Host "`n=== QUEUE ANALYSIS ==="

$uri = "$baseUrl/api/panorama-analysis/${sid}?search_run_id=$SearchRunId"

try {
    $job = Invoke-RestMethod `
        -Method Post `
        -Uri $uri `
        -TimeoutSec 20
}
catch {
    $message = $_.Exception.Message

    if ($_.ErrorDetails.Message) {
        $message += " " + $_.ErrorDetails.Message
    }

    throw "Cannot queue analysis: $message"
}

if ($job.status -eq "already_completed") {
    Write-Host "Analysis already completed"
    return
}

if ($job.status -notin @("queued", "running")) {
    throw "Unexpected queue status: $($job.status)"
}

Write-Host "Job: $($job.job_id)"
Write-Host "Status: $($job.status)"

if ($job.status -eq "running") {
    throw "Job is already running; refusing duplicate execution"
}

Write-Host "`n=== RUN YOLO11N ==="

$worker = "C:\ekolog\scripts\run_yolo_queue_worker.ps1"

if (-not (Test-Path $worker)) {
    throw "YOLO worker not found"
}

& $worker -RunOnce

if (-not $?) {
    throw "YOLO worker failed"
}

Write-Host "`n=== VERIFY RESULTS ==="

$done = Invoke-RestMethod `
    -Uri "$baseUrl/api/yolo-run-detections/$SearchRunId" `
    -TimeoutSec 20

if ($done.fragment_count -ne $expected) {
    throw "Incomplete results: $($done.fragment_count)/$expected"
}

$status = docker exec ecofusion-postgres `
    psql -X -At -U ecofusion -d ecofusion `
    -v ON_ERROR_STOP=1 `
    -c "SELECT status FROM ecofusion.panorama_analysis_jobs WHERE job_id=$($job.job_id);"

if ($LASTEXITCODE -ne 0 -or
    @($status).Count -ne 1 -or
    $status.Trim() -ne "completed") {
    throw "Job completion was not confirmed"
}

Write-Host "Publication: $sid"
Write-Host "Search run: $SearchRunId"
Write-Host "Processed fragments: $($done.fragment_count)"
Write-Host "Detections: $($done.total_detections)"
Write-Host "Gold: SAVED"
Write-Host "Job: COMPLETED"
Write-Host "`nMANUAL ANALYSIS: SUCCESS"
