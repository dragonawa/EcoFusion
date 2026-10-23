param(
    [long]$SilverId = 170,
    [ValidateRange(1,5)]
    [int]$MaxCandidates = 5,
    [ValidateRange(4,7)]
    [int[]]$Parts = @(4,5,6,7)
)

$ErrorActionPreference = "Stop"
Set-Location C:\ekolog

$model = "C:\ekolog\data\grounding_dino\model"
$base = "C:\ekolog\data\panorama_images"
$resultsBase = "C:\ekolog\data\grounding_dino\results"

if (-not (Test-Path "$model\model.safetensors")) {
    throw "Grounding DINO model weights not found"
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
    throw "Cannot read panorama candidates"
}

if ($rows.Count -eq 0) {
    throw "No panorama candidates for publication $SilverId"
}

$directory = Join-Path $base "$SilverId"
New-Item -ItemType Directory -Force -Path $directory |
    Out-Null

foreach ($row in $rows) {
    $fields = $row.Trim() -split '\|'

    if ($fields.Count -ne 3) {
        throw "Unexpected candidate record"
    }

    $candidateId = [long]$fields[0]
    $lat = $fields[1]
    $lon = $fields[2]
    $prefix = "candidate_$candidateId"
    $full = Join-Path $directory "${prefix}_full.jpg"

    Write-Host "`n=== Candidate $candidateId ==="

    if (-not (Test-Path $full)) {
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
            throw "Panorama download failed: $candidateId"
        }
    } else {
        Write-Host "Existing panorama reused"
    }

    foreach ($part in $Parts) {
        $imageName = "${prefix}_part${part}.jpg"
        $imagePath = Join-Path $directory $imageName

        if (-not (Test-Path $imagePath)) {
            throw "Missing panorama fragment: $imageName"
        }

        $resultDir = Join-Path $resultsBase `
            "panorama_${SilverId}_${prefix}_part${part}"

        $resultFile = Join-Path $resultDir "detections.json"

        if (Test-Path $resultFile) {
            Write-Host "Existing detection reused: $imageName"
            continue
        }

        New-Item -ItemType Directory -Force -Path $resultDir |
            Out-Null

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
            -v "${resultDir}:/results" `
            --entrypoint python `
            ecofusion-grounding-dino:cpu `
            /app/detect_waste_grounding_dino.py `
            --image "/images/$imageName" `
            --output /results

        if ($LASTEXITCODE -ne 0) {
            throw "Grounding DINO failed: $imageName"
        }
    }
}

Write-Host "`nSTEP 36.7.271: PANORAMA ANALYSIS COMPLETED"
Write-Host "Publication: $SilverId"
Write-Host "Panorama positions: $($rows.Count)"