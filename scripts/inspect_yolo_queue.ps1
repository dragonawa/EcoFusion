param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $DryRun) {
    throw "Only -DryRun is supported at this stage"
}

$sql = @"
SELECT
    j.job_id,
    j.silver_id,
    COALESCE(j.search_run_id::text, ''),
    j.status,
    COALESCE(r.selected_count, 0),
    (
        SELECT count(*)
        FROM ecofusion.panorama_search_run_candidates m
        WHERE m.search_run_id = j.search_run_id
    ),
    (
        SELECT count(*)
        FROM ecofusion.gold_yolo_run_detections d
        WHERE d.search_run_id = j.search_run_id
    )
FROM ecofusion.panorama_analysis_jobs j
LEFT JOIN ecofusion.panorama_search_runs r
  ON r.search_run_id = j.search_run_id
 AND r.silver_id = j.silver_id
WHERE j.status = 'queued'
ORDER BY j.job_id;
"@

Write-Host "=== YOLO11N QUEUE INSPECTION ==="

$rows = @(
    docker exec ecofusion-postgres `
        psql -X -At -F "|" `
        -U ecofusion -d ecofusion `
        -v ON_ERROR_STOP=1 `
        -c $sql
)

if ($LASTEXITCODE -ne 0) {
    throw "Queue query failed"
}

$eligible = 0
$blocked = 0
$completed = 0

foreach ($line in $rows) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    $fields = $line.Split("|")

    if ($fields.Count -ne 7) {
        throw "Unexpected database response"
    }

    $jobId = [long]$fields[0]
    $sid = [long]$fields[1]
    $runText = $fields[2]
    $status = $fields[3]
    $selected = [int]$fields[4]
    $members = [int]$fields[5]
    $results = [int]$fields[6]

    $reason = ""
    $state = ""

    if ([string]::IsNullOrWhiteSpace($runText)) {
        $reason = "Missing search_run_id"
        $state = "BLOCKED"
        $blocked++
    }
    elseif ($selected -lt 1 -or $selected -gt 5 -or
            $members -ne $selected) {
        $reason = "Invalid candidate membership"
        $state = "BLOCKED"
        $blocked++
    }
    elseif ($results -eq (4 * $selected)) {
        $reason = "All YOLO results already saved"
        $state = "ALREADY COMPLETED"
        $completed++
    }
    elseif ($results -ne 0) {
        $reason = "Partial Gold results require review"
        $state = "BLOCKED"
        $blocked++
    }
    else {
        $reason = "Eligible for input-file verification"
        $state = "READY FOR NEXT CHECK"
        $eligible++
    }

    Write-Host (
        "Job={0}; Publication={1}; Run={2}; State={3}; Reason={4}" -f
        $jobId, $sid, $runText, $state, $reason
    )
}

Write-Host "Queued jobs: $($rows.Count)"
Write-Host "Eligible for next check: $eligible"
Write-Host "Blocked: $blocked"
Write-Host "Already completed: $completed"
Write-Host "Database writes: NONE"
Write-Host "Model inference: NONE"
Write-Host "YANDEX requests: NONE"

Write-Host "QUEUE INSPECTION: SUCCESS"
