param(
    [string]$ImportPath = "data/songs_import.json",
    [string]$TargetPath = "data/songs.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ImportPath)) {
    Write-Error "Import file not found: $ImportPath"
}

if (-not (Test-Path $TargetPath)) {
    Write-Error "Target file not found: $TargetPath"
}

try {
    $existingSongs = Get-Content $TargetPath -Raw | ConvertFrom-Json
} catch {
    Write-Error "Target JSON is invalid: $TargetPath`n$($_.Exception.Message)"
}

try {
    $incomingSongs = Get-Content $ImportPath -Raw | ConvertFrom-Json
} catch {
    Write-Error "Import JSON is invalid: $ImportPath`n$($_.Exception.Message)"
}

if ($null -eq $incomingSongs) {
    Write-Error "Import file is empty: $ImportPath"
}

if ($incomingSongs -isnot [System.Collections.IEnumerable] -or $incomingSongs -is [string]) {
    $incomingSongs = @($incomingSongs)
}

$existingList = @($existingSongs)
$incomingList = @($incomingSongs)

function Safe-Value($value) {
    if ($null -eq $value) { return "" }
    return [string]$value
}

$index = @{}
foreach ($song in $existingList) {
    $key = "{0}|{1}|{2}" -f (Safe-Value $song.title), (Safe-Value $song.artist), (Safe-Value $song.year)
    if (-not $index.ContainsKey($key)) {
        $index[$key] = $true
    }
}

$added = 0
$skipped = 0

foreach ($song in $incomingList) {
    if (-not $song.title -or -not $song.artist -or -not $song.year) {
        $skipped++
        continue
    }

    $key = "{0}|{1}|{2}" -f $song.title, $song.artist, $song.year
    if ($index.ContainsKey($key)) {
        $skipped++
        continue
    }

    $existingList += $song
    $index[$key] = $true
    $added++
}

$existingList | ConvertTo-Json -Depth 8 | Set-Content $TargetPath

# Re-parse written file to ensure it is valid JSON.
try {
    $null = Get-Content $TargetPath -Raw | ConvertFrom-Json
} catch {
    Write-Error "Write succeeded but output JSON is invalid. Please check $TargetPath`n$($_.Exception.Message)"
}

$byYear = $incomingList |
    Where-Object { $_.year } |
    Group-Object year |
    Sort-Object Name |
    ForEach-Object { "{0}: {1}" -f $_.Name, $_.Count }

Write-Host "Imported from: $ImportPath"
Write-Host "Target: $TargetPath"
Write-Host "Added: $added"
Write-Host "Skipped: $skipped"
Write-Host "Incoming by year:"
$byYear | ForEach-Object { Write-Host "  $_" }