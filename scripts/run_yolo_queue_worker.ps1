param(
    [switch]$RunOnce
)

$ErrorActionPreference = "Stop"

Set-Location C:\ekolog

$helper = "/app/dashboard/yolo_queue_db.py"

if (-not $RunOnce) {
    Write-Host "=== YOLO11N WORKER: READ-ONLY INSPECTION ==="

    docker exec ecofusion-dashboard python $helper inspect

    if ($LASTEXITCODE -ne 0) {
        throw "Worker inspection failed"
    }

    Write-Host "WORKER PREFLIGHT: SUCCESS"
    return
}

Write-Host "=== CLAIM ONE QUEUED JOB ==="

$claimText = docker exec ecofusion-dashboard `
    python $helper claim

if ($LASTEXITCODE -ne 0) {
    throw "Queue claim failed"
}

$claim = $claimText | ConvertFrom-Json

if ($claim.status -eq "empty") {
    Write-Host "QUEUE EMPTY: no analysis required"
    return
}

if ($claim.status -ne "claimed") {
    throw "Unexpected queue claim response"
}

$jobId = [long]$claim.job_id
$runId = [long]$claim.search_run_id
$sid = [long]$claim.silver_id

try {
    Write-Host "Job: $jobId; Publication: $sid; Run: $runId"

    $manifest = "C:\ekolog\data\yolo\search_runs\run_${runId}_manifest.json"
    $output = "C:\ekolog\data\yolo\search_run_results"
    $runOutput = Join-Path $output "run_${runId}"

    if (Test-Path $runOutput) {
        throw "Output directory already exists; manual review required"
    }

    # Produce a manifest only if one does not already exist.
    if (-not (Test-Path $manifest)) {
        & C:\ekolog\scripts\prepare_yolo_search_run.ps1 `
            -SearchRunId $runId

        if (-not $?) {
            throw "Manifest creation failed"
        }
    }

    if (-not (Test-Path $manifest)) {
        throw "Manifest is missing"
    }

    New-Item -ItemType Directory `
        -Path $output -Force | Out-Null

    Write-Host "=== RUN YOLO11N ==="

    docker run --rm `
        --network none `
        --cpus 2 `
        --memory 4g `
        -e OMP_NUM_THREADS=1 `
        -e MKL_NUM_THREADS=1 `
        -e OPENBLAS_NUM_THREADS=1 `
        -e YOLO_CONFIG_DIR=/tmp/ultralytics `
        -v "C:\ekolog\scripts:/app/scripts:ro" `
        -v "C:\ekolog\data\yolo\search_runs:/manifests:ro" `
        -v "C:\ekolog\data\yolo\models:/models:ro" `
        -v "C:\ekolog\data\panorama_images:/images:ro" `
        -v "${output}:/results" `
        --entrypoint python `
        ultralytics/ultralytics:latest-cpu `
        /app/scripts/run_yolo_search_run.py `
        --manifest "/manifests/run_${runId}_manifest.json"

    if ($LASTEXITCODE -ne 0) {
        throw "YOLO11n inference failed"
    }

    Write-Host "=== SAVE RESULTS TO GOLD ==="

    docker exec ecofusion-dashboard `
        python $helper finish --job-id $jobId

    if ($LASTEXITCODE -ne 0) {
        throw "Atomic Gold import failed"
    }

    Write-Host "WORKER RUN: SUCCESS"
}
catch {
    $reason = $_.Exception.Message
    Write-Host "WORKER ERROR: $reason"

    docker exec ecofusion-dashboard `
        python $helper fail `
        --job-id $jobId `
        --reason $reason

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Failed to mark job as failed; inspect queue"
    }

    throw
}
