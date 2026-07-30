param(
    [Parameter(Mandatory = $true)]
    [string[]]$Files
)

$certificateBase64 = $env:WINDOWS_CERTIFICATE_BASE64
$certificatePassword = $env:WINDOWS_CERTIFICATE_PASSWORD

if ([string]::IsNullOrWhiteSpace($certificateBase64) -and
    [string]::IsNullOrWhiteSpace($certificatePassword)) {
    Write-Warning "Windows signing secrets are not configured; publishing unsigned artifacts."
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "### Windows signing`nArtifacts were published unsigned."
    }
    exit 0
}

if ([string]::IsNullOrWhiteSpace($certificateBase64) -or
    [string]::IsNullOrWhiteSpace($certificatePassword)) {
    throw "WINDOWS_CERTIFICATE_BASE64 and WINDOWS_CERTIFICATE_PASSWORD must be configured together."
}

$signingDirectory = Join-Path $env:GITHUB_WORKSPACE "workdir\signing"
$certificatePath = Join-Path $signingDirectory "certificate.pfx"
New-Item -ItemType Directory -Force -Path $signingDirectory | Out-Null

try {
    [IO.File]::WriteAllBytes(
        $certificatePath,
        [Convert]::FromBase64String($certificateBase64)
    )
    $signtool = (Get-Command signtool.exe -ErrorAction Stop).Source
    foreach ($file in $Files) {
        $resolved = (Resolve-Path $file -ErrorAction Stop).Path
        & $signtool sign `
            /fd SHA256 `
            /td SHA256 `
            /tr "http://timestamp.digicert.com" `
            /f $certificatePath `
            /p $certificatePassword `
            $resolved
        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed for $resolved"
        }
        & $signtool verify /pa $resolved
        if ($LASTEXITCODE -ne 0) {
            throw "Signature verification failed for $resolved"
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "### Windows signing`nAuthenticode signatures verified successfully."
    }
}
finally {
    if (Test-Path $certificatePath) {
        Remove-Item -Force $certificatePath
    }
}
