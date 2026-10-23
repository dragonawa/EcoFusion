$ErrorActionPreference = "Stop"

$project = "C:\ekolog"
Set-Location $project

$logDir = Join-Path $project "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$log = Join-Path $logDir (
    "pipeline_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log"
)

# Prevent overlapping scheduled runs.
$lockPath = Join-Path $logDir "pipeline.lock"
$lock = $null
$failed = $false

function Log([string]$message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message"
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

try {
    try {
        $lock = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        throw "Another EcoFusion pipeline run is already active"
    }

    # Read credentials locally without printing them.
    $settings = @{}

    foreach ($line in Get-Content ".env" -Encoding UTF8) {
        $line = $line.Trim()

        if (!$line -or $line.StartsWith("#")) {
            continue
        }

        if ($line -match '^([^=]+)=(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim().Trim('"').Trim("'")
            $settings[$key] = $value
        }
    }

    foreach ($key in @(
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "VK_SERVICE_TOKEN"
    )) {
        if ([string]::IsNullOrWhiteSpace($settings[$key])) {
            throw "Required configuration is missing: $key"
        }
    }

    $queries = @(
        "несанкционированная свалка",
        "строительный мусор",
        "свалили мусор",
        "свалка возле дома",
        "мусор рядом с домом",
        "мусор напротив дома",
        "мусор за гаражами",
        "свалка у дороги",
        "мусор около остановки",
        "отходы возле жилых домов"
    ) -join ";"

    $commonArgs = @(
        "run", "--rm",
        "--network", "ecofusion_default",
        "--env-file", ".env",
        "-e", "POSTGRES_HOST=ecofusion-postgres",
        "-e", "POSTGRES_PORT=5432",
        "-e", "S3_ENDPOINT=http://ecofusion-seaweedfs:8333",
        "-e", "S3_ACCESS_KEY=$($settings['MINIO_ROOT_USER'])",
        "-e", "S3_SECRET_KEY=$($settings['MINIO_ROOT_PASSWORD'])",
        "-e", "BRONZE_BUCKET=ecofusion-bronze"
    )

    function Run-Stage(
        [string]$name,
        [string]$module,
        [string[]]$extraEnv = @()
    ) {
        Log "START: $name"

        $args = @($commonArgs)

        foreach ($variable in $extraEnv) {
            $args += @("-e", $variable)
        }

        $args += @(
            "ecofusion-pipeline:dev",
            "python", "-m", $module
        )

        & docker @args 2>&1 |
            Tee-Object -FilePath $log -Append

        if ($LASTEXITCODE -ne 0) {
            throw "$name failed with exit code $LASTEXITCODE"
        }

        Log "SUCCESS: $name"
    }

    Log "=== ECOFUSION PIPELINE STARTED ==="

    Run-Stage `
        "Bronze VK collector" `
        "pipeline.bronze.collectors.vk_collector" `
        @(
            "VK_COUNT=10",
            "VK_SEARCH_QUERIES=$queries"
        )

    Run-Stage `
        "Silver transform" `
        "pipeline.silver.transform"

    Run-Stage `
        "Silver classification" `
        "pipeline.silver.classify"

    Log "START: Geographic analysis"

    $geoArgs = @(
        "run", "--rm",
        "--network", "ecofusion_default",
        "--env-file", ".env",
        "-e", "POSTGRES_HOST=ecofusion-postgres",
        "-e", "POSTGRES_PORT=5432",
        "-v", "C:\ekolog\scripts\refresh_geo_candidates.py:/app/refresh_geo_candidates.py:ro",
        "-v", "C:\ekolog\pipeline\silver\spatial_context.py:/app/spatial_context.py:ro",
        "-v", "C:\ekolog\pipeline\silver\address_landmarks.py:/app/address_landmarks.py:ro",
        "-v", "C:\ekolog\data:/data",
        "-w", "/app",
        "--entrypoint", "python",
        "ecofusion-dashboard:dev",
        "/app/refresh_geo_candidates.py"
    )

    & docker @geoArgs 2>&1 |
        Tee-Object -FilePath $log -Append

    if ($LASTEXITCODE -ne 0) {
        throw "Geographic analysis failed with exit code $LASTEXITCODE"
    }

    Log "SUCCESS: Geographic analysis"
    Log "=== ECOFUSION PIPELINE SUCCESS ==="
}
catch {
    $failed = $true
    Log "PIPELINE ERROR: $($_.Exception.Message)"
}
finally {
    if ($null -ne $lock) {
        $lock.Dispose()
    }
}

if ($failed) {
    exit 1
}

exit 0

