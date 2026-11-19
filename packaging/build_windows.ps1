# Windows：构建 BidCraft.exe 并打包 zip（在 Windows 上执行）。
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$version = (uv run python -c "from bidcraft import __version__; print(__version__)").Trim()
if (-not (Test-Path "dist/BidCraft")) {
    uv run pyinstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/bidcraft.spec
}
Compress-Archive -Path "dist/BidCraft" -DestinationPath "dist/bidcraft-desktop-$version-windows.zip" -Force
Write-Host "产物：dist/bidcraft-desktop-$version-windows.zip"
