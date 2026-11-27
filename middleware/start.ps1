# Compatibility entry point for local middleware on Docker Desktop.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $projectRoot 'compose.yml'
$settings = Join-Path $PSScriptRoot '.env'

if (!(Test-Path -LiteralPath $settings -PathType Leaf)) {
    throw 'Prepare middleware/.env with two unique keys first. See README.md, or use the Linux installer in WSL.'
}
& docker compose --project-directory $projectRoot --file $compose --env-file $settings up --detach --build --wait
exit $LASTEXITCODE
