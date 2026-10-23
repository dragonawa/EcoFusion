param(
    [Parameter(Mandatory=$true)]
    [ValidateRange(1,9223372036854775807)]
    [long]$SearchRunId
)

$ErrorActionPreference = "Stop"
Set-Location C:\ekolog

$model = "C:\ekolog\data\yolo\models\ecofusion_yolo11n_best.pt"

if (-not (Test-Path $model)) {
    throw "YOLO11n model missing"
}

$sql = @"
SELECT
    r.silver_id,
    m.selection_order,
    m.candidate_id,
    m.discovery_source,
    c.panorama_latitude,
    c.panorama_longitude,
    r.search_report->>'test_replay'
FROM ecofusion.panorama_search_runs r
JOIN ecofusion.panorama_search_run_candidates m
  ON m.search_run_id=r.search_run_id
JOIN ecofusion.gold_panorama_candidates c
  ON c.candidate_id=m.candidate_id
 AND c.silver_id=r.silver_id
WHERE r.search_run_id=$SearchRunId
ORDER BY m.selection_order;
"@

$rows = @(
    $sql | docker exec -i ecofusion-postgres `
        psql -X -At -F "|" -v ON_ERROR_STOP=1 `
        -U ecofusion -d ecofusion
)

if ($LASTEXITCODE -ne 0) {
    throw "Cannot read search-run candidates"
}

if ($rows.Count -eq 0 -or $rows.Count -gt 5) {
    throw "Expected 1 to 5 candidates for this search run"
}

$manifest = @()

foreach ($row in $rows) {
    $fields = $row.Trim() -split '\|', 7

    if ($fields.Count -ne 7) {
        throw "Invalid candidate record"
    }

    $sid = [long]$fields[0]
    $ordinal = [int]$fields[1]
    $candidateId = [long]$fields[2]
    $origin = $fields[3]

    $dir = "C:\ekolog\data\panorama_images\$sid"
    $prefix = "candidate_$candidateId"

    $full = Join-Path $dir "${prefix}_full.jpg"

    $parts = @(
        foreach ($part in 4..7) {
            $image = Join-Path $dir "${prefix}_part${part}.jpg"

            [pscustomobject]@{
                part = $part
                path = $image
                exists = Test-Path $image
            }
        }
    )

    $manifest += [pscustomobject]@{
        search_run_id = $SearchRunId
        silver_id = $sid
        selection_order = $ordinal
        candidate_id = $candidateId
        discovery_source = $origin
        latitude = [double]::Parse(
            $fields[4],
            [Globalization.CultureInfo]::InvariantCulture
        )
        longitude = [double]::Parse(
            $fields[5],
            [Globalization.CultureInfo]::InvariantCulture
        )
        test_replay = ($fields[6] -eq "true")
        full_image_exists = Test-Path $full
        fragments = $parts
    }
}

if (@($manifest | Select-Object -ExpandProperty silver_id -Unique).Count -ne 1) {
    throw "Mixed publication IDs in one search run"
}

$destination = "C:\ekolog\data\yolo\search_runs"
New-Item -ItemType Directory -Path $destination -Force |
    Out-Null

$output = Join-Path $destination "run_${SearchRunId}_manifest.json"

if (Test-Path $output) {
    throw "Manifest already exists; not overwriting: $output"
}

$manifest | ConvertTo-Json -Depth 8 |
    Set-Content $output -Encoding UTF8

Write-Host "=== SEARCH RUN MANIFEST ==="
Write-Host "Search run: $SearchRunId"
Write-Host "Candidates: $($manifest.Count)"
Write-Host "Test replay: $($manifest[0].test_replay)"
Write-Host "Manifest: $output"

$manifest | ForEach-Object {
    $ready = @($_.fragments | Where-Object exists).Count
    Write-Host (
        "Candidate {0} | {1} | full={2} | fragments={3}/4" -f `
        $_.candidate_id,
        $_.discovery_source,
        $_.full_image_exists,
        $ready
    )
}

Write-Host "STEP 36.7.391: MANIFEST READY"
