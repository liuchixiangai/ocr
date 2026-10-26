<#
Signs Windows executables with Azure Artifact Signing (formerly Trusted
Signing), with an RFC 3161 timestamp, then checks each file's signature.

    pwsh scripts/ci/sign_windows.ps1 <file> [<file> ...]

The signing account comes from environment variables:

    AZURE_SIGNING_ENDPOINT  the account's endpoint, e.g. https://eus.codesigning.azure.net/
    AZURE_SIGNING_ACCOUNT   the Artifact Signing account
    AZURE_SIGNING_PROFILE   the certificate profile

and the identity from AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET
(an app registration with the "Artifact Signing Certificate Profile Signer"
role, as in CI) or, without them, from `az login`.

build.yml runs it on the built scoresight.exe, and Inno Setup runs it as
scoresight.iss's SignTool on Setup and the uninstaller.
#>
param(
    [Parameter(Mandatory, ValueFromRemainingArguments)]
    [string[]] $Files
)

$ErrorActionPreference = 'Stop'

# The module that azure/artifact-signing-action v2.0.0 uses. It downloads
# signtool and the Artifact Signing client from NuGet on first use.
$ModuleVersion = '0.1.20'

foreach ($name in 'AZURE_SIGNING_ENDPOINT', 'AZURE_SIGNING_ACCOUNT', 'AZURE_SIGNING_PROFILE') {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        throw "$name is not set"
    }
}
# the module needs absolute paths, separated by commas
$paths = foreach ($file in $Files) { (Resolve-Path -LiteralPath $file).Path }

if (-not (Get-Module -ListAvailable -Name ArtifactSigning | Where-Object Version -EQ $ModuleVersion)) {
    Install-Module -Name ArtifactSigning -RequiredVersion $ModuleVersion -Repository PSGallery -Scope CurrentUser -Force
}
Import-Module -Name ArtifactSigning -RequiredVersion $ModuleVersion

$signing = @{
    Endpoint                            = $env:AZURE_SIGNING_ENDPOINT
    CodeSigningAccountName              = $env:AZURE_SIGNING_ACCOUNT
    CertificateProfileName              = $env:AZURE_SIGNING_PROFILE
    Files                               = $paths -join ','
    FileDigest                          = 'SHA256'
    TimestampRfc3161                    = 'http://timestamp.acs.microsoft.com'
    TimestampDigest                     = 'SHA256'
    # the program name and link of Windows' UAC prompt
    Description                         = 'ScoreSight'
    DescriptionUrl                      = 'https://github.com/royshil/scoresight'
    # only the environment variables or `az login`, as the action does
    ExcludeWorkloadIdentityCredential   = $true
    ExcludeManagedIdentityCredential    = $true
    ExcludeSharedTokenCacheCredential   = $true
    ExcludeVisualStudioCredential       = $true
    ExcludeVisualStudioCodeCredential   = $true
    ExcludeAzurePowerShellCredential    = $true
    ExcludeAzureDeveloperCliCredential  = $true
    ExcludeInteractiveBrowserCredential = $true
}
Invoke-ArtifactSigning @signing

foreach ($path in $paths) {
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    # a signature without a timestamp would lapse with the 3-day certificate
    if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate) {
        throw "$path has no valid timestamped signature: $($signature.Status) $($signature.StatusMessage)"
    }
    Write-Host "Signed $path ($($signature.SignerCertificate.Subject))"
}
