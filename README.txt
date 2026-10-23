EcoFusion local dashboard (first version)

1. Unpack dashboard/ directory to C:\ekolog\dashboard\ (do NOT overwrite pipeline/ or .env).
2. Copy start_dashboard.ps1 to C:\ekolog\start_dashboard.ps1.
3. Ensure docker compose infrastructure is running and ecofusion-pipeline:dev exists.
4. In PowerShell:
   Set-Location C:\ekolog
   .\start_dashboard.ps1
5. Open http://localhost:8787

The dashboard reads real Silver, attachment, classification and Gold records.
It shows full text, source links, all raw JSON and photos when VK payload contains
usable photo URLs. GEO candidate data are loaded from existing JSON files.
It is read-only: the Check button performs an honest preflight only; it does NOT
claim to run Yandex panoramas or ML. Automatic verification is a later integration.
Keep .env and your full data sets OUT of GitHub; this bundle has neither.
