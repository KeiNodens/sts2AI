param(
    [Parameter(Mandatory=$true)][string]$GameDir,
    [ValidateSet("host", "join")][string]$Role = "join"
)
$ErrorActionPreference = "Stop"
$game = (Resolve-Path -LiteralPath $GameDir).Path
$exe = Join-Path $game "SlayTheSpire2.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "SlayTheSpire2.exe not found in the selected directory" }
$env:STS2_MCP_PORT = if ($Role -eq "host") { "15526" } else { "15527" }
Start-Process -FilePath $exe -ArgumentList @("--fastmp", $Role) -WorkingDirectory $game
