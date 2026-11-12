# Builds the frozen server sidecar and places it where Tauri expects it.
# Run from the repository root: .\desktop\build-sidecar.ps1
# Works in Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = "Stop"

# PyInstaller logs INFO to stderr. Under Windows PowerShell 5.1 a native
# command's stderr becomes error records, which "Stop" turns into a terminating
# error on the first log line. So relax the preference around the call only and
# judge success by the exit code instead.
# --collect-data tzdata: the timezone preference validates against zoneinfo,
# which has no OS database on Windows — the frozen exe must carry tzdata's files.
# --collect-data app: app/static (theme.css, insights.js, icons, preview image)
# is read from disk at runtime — without it every page renders unstyled.
$ErrorActionPreference = "Continue"
.venv\Scripts\pyinstaller --onefile --name owlet-server `
  --distpath desktop\dist --workpath desktop\build --specpath desktop `
  --paths . --collect-submodules uvicorn --collect-submodules app `
  --collect-data tzdata --collect-data app `
  --hidden-import aiosqlite --noconfirm desktop\server_entry.py 2>&1 |
  ForEach-Object { "$_" }
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($pyiExit -ne 0) {
    throw "PyInstaller failed with exit code $pyiExit"
}
if (-not (Test-Path desktop\dist\owlet-server.exe)) {
    throw "PyInstaller reported success but desktop\dist\owlet-server.exe was not produced"
}

$target = "desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force $target | Out-Null
# Tauri sidecars are resolved by target triple suffix.
Copy-Item desktop\dist\owlet-server.exe "$target\owlet-server-x86_64-pc-windows-msvc.exe" -Force
Write-Host "Sidecar ready: $target\owlet-server-x86_64-pc-windows-msvc.exe"
