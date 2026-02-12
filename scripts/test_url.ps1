param(
  [Parameter(Mandatory=$true)][string]$Url,
  [string]$Worksheet = "Goods_Test",
  [switch]$Write,
  [switch]$PostDiscord,
  [string]$RepoRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

Set-Location (Resolve-Path "$RepoRoot/..")

# Load .env if exists
$envPath = Join-Path (Get-Location) ".env"
if (Test-Path $envPath) {
  (Get-Content $envPath) | ForEach-Object {
    if ($_ -match '^(\s*#|\s*$)') { return }
    $kv = $_.Split('=',2)
    if ($kv.Length -eq 2) { [System.Environment]::SetEnvironmentVariable($kv[0], $kv[1]) }
  }
}

# Prefer venv python if available
$py = Join-Path ".venv/Scripts" "python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$argsList = @('-m','holo_monitor.test_url','--url',"$Url",'--worksheet',"$Worksheet")
if ($Write) { $argsList += '--write' }
if ($PostDiscord) { $argsList += '--post-discord' }

& $py @argsList
exit $LASTEXITCODE

