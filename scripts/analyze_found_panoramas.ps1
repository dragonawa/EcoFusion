param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9223372036854775807)]
    [long]$SearchRunId
)

$ErrorActionPreference = "Stop"
Set-Location C:\ekolog

Write-Host "=== ECOFUSION: PANORAMA -> YOLO11N ==="
Write-Host "Search run: $SearchRunId"

# Obtain the exact candidates belonging to this search run.
$sql = @"
SELECT
    r.silver_id,
    m.candidate_id,
    c.panorama_latitude,
    c.panorama_longitude,
    m.selection_order
FROM ecofusion.panorama_search_runs r
JOIN ecofusion.panorama_search_run_candidates m
  ON m.search_run_id = r.search_run_id
JOIN ecofusion.gold_panorama_candidates c
  ON c.candidate_id = m.candidate_id
 AND c.silver_id = r.silver_id
WHERE r.search_run_id = $SearchRunId
ORDER BY m.selection_order;
"@

$raw = @(
    $sql | docker exec -i ecofusion-postgres `
        psql -X -At -F "|" `
        -U ecofusion -d ecofusion `
        -v ON_ERROR_STOP=1
)

if ($LASTEXITCODE -ne 0) {
    throw "Cannot load search run from PostgreSQL"
}

$rows = @($raw | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_)
})

if ($rows.Count -lt 1 -or $rows.Count -gt 5) {
    throw "Search run must contain 1 to 5 candidates"
}

$candidates = @()
$seen = @{}

foreach ($line in $rows) {
    $fields = $line.Split("|")

    if ($fields.Count -ne 5) {
        throw "Invalid candidate record"
    }

    $sid = [long]$fields[0]
    $cid = [long]$fields[1]
    $lat = [double]::Parse(
        $fields[2],
        [Globalization.CultureInfo]::InvariantCulture
    )
    $lon = [double]::Parse(
        $fields[3],
        [Globalization.CultureInfo]::InvariantCulture
    )
    $order = [int]$fields[4]

    if (
        $sid -le 0 -or $cid -le 0 -or
        [double]::IsNaN($lat) -or
        [double]::IsNaN($lon) -or
        [Math]::Abs($lat) -gt 90 -or
        [Math]::Abs($lon) -gt 180
    ) {
        throw "Invalid candidate coordinates or identifiers"
    }

    if ($seen.ContainsKey($cid)) {
        throw "Duplicate candidate in search run"
    }

    $seen[$cid] = $true

    $candidates += [pscustomobject]@{
        SilverId = $sid
        CandidateId = $cid
        Latitude = $lat
        Longitude = $lon
        Order = $order
    }
}

$publication = $candidates[0].SilverId

if (@($candidates | Where-Object {
    $_.SilverId -ne $publication
}).Count -gt 0) {
    throw "Candidates belong to different publications"
}

Write-Host "Publication: $publication"
Write-Host "Panoramas: $($candidates.Count)"

# Validate selected-count metadata.
$expectedCount = @(
    docker exec ecofusion-postgres `
        psql -X -At -U ecofusion -d ecofusion `
        -v ON_ERROR_STOP=1 `
        -c "SELECT selected_count FROM ecofusion.panorama_search_runs WHERE search_run_id=$SearchRunId;"
)

if (
    $LASTEXITCODE -ne 0 -or
    $expectedCount.Count -ne 1 -or
    [int]$expectedCount[0] -ne $candidates.Count
) {
    throw "Search run candidate count mismatch"
}

# If YOLO results already exist, do not download or analyze again.
$existing = Invoke-RestMethod `
    -Uri "http://localhost:8787/api/yolo-run-detections/$SearchRunId" `
    -TimeoutSec 20

$expectedFragments = 4 * $candidates.Count

if ($existing.fragment_count -eq $expectedFragments) {
    Write-Host "Analysis already completed"
    Write-Host "Detections: $($existing.total_detections)"
    Write-Host "ECOFUSION: ALREADY COMPLETED"
    return
}

if ($existing.fragment_count -ne 0) {
    throw "Partial Gold results exist; review required"
}

# Avoid accidentally claiming another queued publication's job.
$active = @(
    docker exec ecofusion-postgres `
        psql -X -At -U ecofusion -d ecofusion `
        -v ON_ERROR_STOP=1 `
        -c "SELECT count(*) FROM ecofusion.panorama_analysis_jobs WHERE status IN ('queued','running');"
)

if (
    $LASTEXITCODE -ne 0 -or
    $active.Count -ne 1 -or
    [int]$active[0] -ne 0
) {
    throw "Another analysis job is active; process it first"
}

# Check local images and acquire only missing complete candidates.
Write-Host "`n=== PREPARE PANORAMA IMAGES ==="

$baseFolder = "C:\ekolog\data\panorama_images\$publication"

New-Item -ItemType Directory -Force `
    -Path $baseFolder | Out-Null

foreach ($candidate in $candidates) {
    $cid = $candidate.CandidateId
    $prefix = "candidate_$cid"

    $required = @("${prefix}_full.jpg")
    $required += @(4..7 | ForEach-Object {
        "${prefix}_part$_.jpg"
    })
    $required += @("${prefix}_metadata.json")

    $present = @(
        $required | Where-Object {
            Test-Path (Join-Path $baseFolder $_)
        }
    )

    if ($present.Count -eq $required.Count) {
        Write-Host "Candidate $cid : images already saved"
        continue
    }

    if ($present.Count -gt 0) {
        throw "Candidate $cid has incomplete files; refusing overwrite"
    }

    Write-Host "Downloading panorama candidate $cid ..."

    $lat = $candidate.Latitude.ToString(
        "G17",
        [Globalization.CultureInfo]::InvariantCulture
    )
    $lon = $candidate.Longitude.ToString(
        "G17",
        [Globalization.CultureInfo]::InvariantCulture
    )

    docker run --rm `
        --network bridge `
        --cpus 2 `
        --memory 4g `
        -v "C:\ekolog\scripts:/scripts:ro" `
        -v "C:\ekolog\data\panorama_images:/images" `
        --entrypoint python `
        ultralytics/ultralytics:latest-cpu `
        /scripts/save_yandex_panorama_legacy.py `
        --lat $lat `
        --lon $lon `
        --output "/images/$publication" `
        --prefix $prefix `
        --zoom 0

    if ($LASTEXITCODE -ne 0) {
        throw "Panorama download failed for candidate $cid"
    }

    foreach ($name in $required) {
        if (-not (Test-Path (Join-Path $baseFolder $name))) {
            throw "Panorama image preparation incomplete: $name"
        }
    }

    Write-Host "Candidate $cid : SAVED"
}

Write-Host "`n=== VERIFY ALL IMAGE FRAGMENTS ==="

foreach ($candidate in $candidates) {
    $cid = $candidate.CandidateId

    foreach ($part in 4..7) {
        $image = Join-Path $baseFolder `
            "candidate_${cid}_part${part}.jpg"

        if (-not (Test-Path $image)) {
            throw "Missing source image: $image"
        }
    }
}

Write-Host "Image fragments ready: $expectedFragments"

Write-Host "`n=== RUN EXISTING YOLO11N PIPELINE ==="

& "C:\ekolog\scripts\analyze_panorama.ps1" `
    -SearchRunId $SearchRunId

if (-not $?) {
    throw "YOLO11n analysis failed"
}

Write-Host "`n=== VERIFY GOLD ==="

$done = Invoke-RestMethod `
    -Uri "http://localhost:8787/api/yolo-run-detections/$SearchRunId" `
    -TimeoutSec 20

if (
    $done.silver_id -ne $publication -or
    $done.fragment_count -ne $expectedFragments
) {
    throw "Gold results are incomplete"
}

Write-Host "Publication: $publication"
Write-Host "Search run: $SearchRunId"
Write-Host "Panoramas: $($candidates.Count)"
Write-Host "Analyzed fragments: $($done.fragment_count)"
Write-Host "Detected objects: $($done.total_detections)"
Write-Host "Gold: SAVED"

Write-Host "`nECOFUSION PANORAMA ANALYSIS: SUCCESS"
