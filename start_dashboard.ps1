# Run from C:\ekolog after copying dashboard\ here.
$ErrorActionPreference = 'Stop'
Set-Location 'C:\ekolog'
if (-not (Test-Path '.\dashboard\server.py')) { throw 'Missing C:\ekolog\dashboard\server.py' }
if (-not (Test-Path '.\.env')) { throw 'Missing C:\ekolog\.env' }
if (-not (Test-Path '.\data')) { throw 'Missing C:\ekolog\data' }
Write-Host 'EcoFusion dashboard: http://localhost:8787'
docker run --rm --name ecofusion-dashboard `
    --network ecofusion_default `
    --env-file .env `
    -e 'POSTGRES_HOST=ecofusion-postgres' `
    -e 'POSTGRES_PORT=5432' `
    -e 'DASHBOARD_PORT=8787' `
    -p '127.0.0.1:8787:8787' `
    -v 'C:\ekolog\dashboard:/app/dashboard:ro' `
    -v 'C:\ekolog\data:/data:ro' `
    --entrypoint python `
    ecofusion-dashboard:dev `
    /app/dashboard/server.py
if ($LASTEXITCODE -ne 0) { throw 'Dashboard exited with an error' }

