param(
    [ValidateRange(1, 9223372036854775807)]
    [long]$SilverId = 170,

    [ValidateRange(1, 5)]
    [int]$MaxCandidates = 5
)

$ErrorActionPreference = "Stop"
Set-Location C:\ekolog

$model = "C:\ekolog\data\grounding_dino\model"
$base = "C:\ekolog\data\panorama_images"
$resultsBase = "C:\ekolog\data\grounding_dino\results"
$directory = Join-Path $base "$SilverId"

if (-not (Test-Path "$model\model.safetensors")) {
    throw "Local Grounding DINO weights not found"
}

$sql = @"
SELECT candidate_id, panorama_latitude, panorama_longitude
FROM ecofusion.gold_panorama_candidates
WHERE silver_id = $SilverId
ORDER BY candidate_id
LIMIT $MaxCandidates;
"@

$rows = @(
    $sql | docker exec -i ecofusion-postgres sh -c `
        'exec psql -X -At -F "|" -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
)

if ($LASTEXITCODE -ne 0) {
    throw "Failed to read panorama candidates"
}

if ($rows.Count -eq 0) {
    throw "No panorama candidates found"
}

New-Item -ItemType Directory -Force -Path $directory |
    Out-Null

New-Item -ItemType Directory -Force -Path $resultsBase |
    Out-Null

# Phase 1: obtain missing panorama images only.
foreach ($row in $rows) {
    $fields = $row.Trim() -split '\|'

    if ($fields.Count -ne 3) {
        throw "Invalid panorama candidate record"
    }

    $candidateId = [long]$fields[0]
    $lat = $fields[1]
    $lon = $fields[2]

    $prefix = "candidate_$candidateId"

    $expected = @(
        "${prefix}_full.jpg"
        "${prefix}_part4.jpg"
        "${prefix}_part5.jpg"
        "${prefix}_part6.jpg"
        "${prefix}_part7.jpg"
        "${prefix}_metadata.json"
    )

    $missing = @(
        $expected | Where-Object {
            -not (Test-Path (Join-Path $directory $_))
        }
    )

    if ($missing.Count -gt 0) {
        Write-Host "Downloading panorama candidate $candidateId"

        docker run --rm `
            -v "C:\ekolog\scripts:/app/scripts:ro" `
            -v "${base}:/app/output" `
            --entrypoint python `
            ecofusion-grounding-dino:cpu `
            /app/scripts/save_yandex_panorama_legacy.py `
            --lat $lat `
            --lon $lon `
            --output "/app/output/$SilverId" `
            --prefix $prefix `
            --zoom 2

        if ($LASTEXITCODE -ne 0) {
            throw "Panorama saving failed: $candidateId"
        }
    }
    else {
        Write-Host "Panorama candidate $candidateId reused"
    }

    foreach ($file in $expected) {
        if (-not (Test-Path (Join-Path $directory $file))) {
            throw "Missing panorama output: $file"
        }
    }
}

# Pass only the candidates selected from PostgreSQL.
$candidateIds = @(
    foreach ($row in $rows) {
        $fields = $row.Trim() -split '\|'

        if ($fields.Count -ne 3) {
            throw "Invalid candidate record"
        }

        [string]([long]$fields[0])
    }
)

$candidateArguments = @("--candidate-ids") + $candidateIds

# Phase 2: one Grounding DINO process for all available fragments.
Write-Host "`n=== BATCH GROUNDING DINO ==="

docker run --rm `
    --network none `
    -e HF_HUB_OFFLINE=1 `
    -e TRANSFORMERS_OFFLINE=1 `
    -e OMP_NUM_THREADS=1 `
    -e MKL_NUM_THREADS=1 `
    -e OPENBLAS_NUM_THREADS=1 `
    -v "C:\ekolog\scripts:/app:ro" `
    -v "${model}:/models/grounding-dino-tiny:ro" `
    -v "${directory}:/images:ro" `
    -v "${resultsBase}:/results" `
    --entrypoint python `
    ecofusion-grounding-dino:cpu `
    -u /app/detect_waste_grounding_dino_batch.py `
    --images /images `
    --output /results `
    --silver-id $SilverId `
    @candidateArguments

if ($LASTEXITCODE -ne 0) {
    throw "Batch Grounding DINO failed"
}

# Phase 3: verify all requested candidate results.
$completed = 0
$total = 0

foreach ($row in $rows) {
    $candidateId = [long](($row.Trim() -split '\|')[0])
    $ready = 0
    $count = 0

    foreach ($part in 4..7) {
        $resultFile = Join-Path $resultsBase `
            "panorama_${SilverId}_candidate_${candidateId}_part${part}\detections.json"

        if (Test-Path $resultFile) {
            $data = Get-Content $resultFile -Raw -Encoding UTF8 |
                ConvertFrom-Json

            $count += @($data.detections).Count
            $ready++
        }
    }

    if ($ready -eq 4) {
        $completed++
    }

    $total += $count

    Write-Host "Candidate ${candidateId}: $ready/4 fragments; $count detections"
}

Write-Host "`nCompleted panoramas: $completed/$($rows.Count)"
Write-Host "Raw detections: $total"

if ($completed -ne $rows.Count) {
    throw "Not all panorama fragments were analyzed"
}

Write-Host "STEP 36.7.282: BATCH ANALYSIS SUCCESS"