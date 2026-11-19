# Applies a (new) client secret of the IAP sign-in client "coach-iap" to IAP.
# Paste the secret at the hidden prompt. It is written to a temp file only for the gcloud
# call, deleted right after, and never printed.
#   powershell -File scripts/set_iap_secret.ps1 -ClientId <id>.apps.googleusercontent.com [-Project <id>]
param(
    [Parameter(Mandatory = $true)][string]$ClientId,
    [string]$Project = "adaptive-ai-coach"
)
$ErrorActionPreference = "Stop"
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if ($ClientId -notmatch '^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$') {
    Write-Host "That does not look like an OAuth client ID. Nothing changed."; exit 2
}
$clientId = $ClientId

$secure = Read-Host -Prompt "Paste the NEW coach-iap client secret (hidden)" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()
if ($plain -notmatch '^GOCSPX-') { Write-Host "That does not look like a client secret. Nothing changed."; exit 2 }

$file = Join-Path $env:TEMP ("iap_" + [guid]::NewGuid().ToString("N") + ".yaml")
try {
    "access_settings:`n  oauth_settings:`n    client_id: $clientId`n    client_secret: $plain" |
        Set-Content -Path $file -Encoding utf8
    & $gcloud iap settings set $file --project=$Project --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "gcloud could not apply it (exit $LASTEXITCODE)."; exit 1 }
    Write-Host "Applied: IAP now uses the secret ending ...$($plain.Substring($plain.Length - 4))."
} finally {
    if (Test-Path $file) { Remove-Item $file -Force }
    $plain = $null
}
