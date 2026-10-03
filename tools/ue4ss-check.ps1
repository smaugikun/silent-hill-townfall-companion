<#
.SYNOPSIS
    Checks the "UE4SS for SILENT HILL Townfall" (Nexus Mods #4) install and
    optionally deploys the TownfallCompanion mod into it.

.DESCRIPTION
    Read-only by default. With -Install it mirrors this repo's TownfallCompanion folder into
    <Win64>\ue4ss\Mods\TownfallCompanion, keeping what the user has there: companion.ini, cache\
    and tools\. It never touches the proxy DLL, UE4SS_Signatures, UE4SS settings or other mods.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\ue4ss-check.ps1
    powershell -ExecutionPolicy Bypass -File tools\ue4ss-check.ps1 -Install
#>
param(
    [string]$GameDir,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
$SteamAppId = 1636440
$ModName = 'TownfallCompanion'

# The Townfall install folder (...\steamapps\common\Townfall) from Steam's library list, or $null.
function Find-GameDir {
    $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
    if (-not $steam) { return $null }
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (-not (Test-Path $vdf)) { return $null }
    foreach ($m in Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"') {
        $lib = $m.Matches[0].Groups[1].Value -replace '\\\\', '\'
        $acf = Join-Path $lib "steamapps\appmanifest_$SteamAppId.acf"
        if (Test-Path $acf) {
            $installDir = (Select-String -Path $acf -Pattern '"installdir"\s+"([^"]+)"').Matches[0].Groups[1].Value
            return Join-Path $lib "steamapps\common\$installDir"
        }
    }
    return $null
}
$script:failed = $false

function Report([string]$level, [string]$label, [string]$detail) {
    $line = "[{0,-4}] {1}" -f $level, $label
    if ($detail) { $line += ": $detail" }
    $color = @{ OK = 'Green'; WARN = 'Yellow'; FAIL = 'Red'; INFO = 'Gray' }[$level]
    Write-Host $line -ForegroundColor $color
    if ($level -eq 'FAIL') { $script:failed = $true }
}

if (-not $GameDir) { $GameDir = Find-GameDir }
if (-not $GameDir -or -not (Test-Path $GameDir)) {
    Report FAIL 'Townfall install' 'not found via Steam; pass -GameDir <path to ...\common\Townfall>'
    exit 1
}
Report OK 'Townfall install' $GameDir

$acf = Get-ChildItem (Split-Path (Split-Path $GameDir)) -Filter "appmanifest_$SteamAppId.acf" -ErrorAction SilentlyContinue
if ($acf) {
    $build = (Select-String -Path $acf.FullName -Pattern '"buildid"\s+"(\d+)"').Matches[0].Groups[1].Value
    Report INFO 'Steam build' $build
}

$win64 = Join-Path $GameDir 'Townfall\Binaries\Win64'
if (-not (Test-Path $win64)) {
    Report FAIL 'Win64 directory' "missing: $win64"
    exit 1
}
Report OK 'Win64 directory' $win64

# Any top-level DLL the game's own manifest doesn't list was added by a mod loader.
$manifest = Join-Path $GameDir 'Manifest_NonUFSFiles_Win64.txt'
if (Test-Path $manifest) {
    $shipped = @{}
    foreach ($line in Get-Content $manifest) {
        if (($line -split "`t")[0] -match '^Townfall/Binaries/Win64/([^/]+)$') { $shipped[$Matches[1]] = $true }
    }
    $proxies = @(Get-ChildItem $win64 -Filter *.dll -File | Where-Object { -not $shipped.ContainsKey($_.Name) })
    switch ($proxies.Count) {
        0 { Report FAIL 'UE4SS proxy DLL' 'no non-game DLL in Win64, nothing will inject UE4SS' }
        1 { Report OK 'UE4SS proxy DLL' $proxies[0].Name }
        default { Report WARN 'UE4SS proxy DLL' ("several non-game DLLs: " + ($proxies.Name -join ', ')) }
    }
} else {
    Report WARN 'UE4SS proxy DLL' 'game manifest missing, cannot tell game DLLs from added ones'
}

$ue4ss = Join-Path $win64 'ue4ss'
if (-not (Test-Path $ue4ss)) {
    Report FAIL 'ue4ss directory' "missing: $ue4ss"
    Write-Host "`nInstall 'UE4SS for SILENT HILL Townfall' (Nexus Mods #4, by AeonGreyh) into:`n  $win64"
    exit 1
}
Report OK 'ue4ss directory' $ue4ss

$dll = Join-Path $ue4ss 'UE4SS.dll'
if (Test-Path $dll) {
    $v = (Get-Item $dll).VersionInfo
    $version = if ($v.ProductVersion) { $v.ProductVersion } else { 'no version resource, see UE4SS.log header' }
    Report OK 'UE4SS.dll' $version
} else {
    Report FAIL 'UE4SS.dll' "missing: $dll"
}

$signature = Join-Path $ue4ss 'UE4SS_Signatures\StaticConstructObject.lua'
if (Test-Path $signature) {
    Report OK 'Townfall StaticConstructObject signature' ("{0:yyyy-MM-dd HH:mm}" -f (Get-Item $signature).LastWriteTime)
} else {
    Report FAIL 'Townfall StaticConstructObject signature' "missing: $signature (generic UE4SS may crash Townfall)"
}

$settings = Join-Path $ue4ss 'UE4SS-settings.ini'
if (Test-Path $settings) { Report OK 'UE4SS-settings.ini' } else { Report WARN 'UE4SS-settings.ini' 'missing' }

$mods = Join-Path $ue4ss 'Mods'
if (-not (Test-Path $mods)) {
    Report FAIL 'Mods directory' "missing: $mods"
} else {
    Report OK 'Mods directory' $mods
    $listed = @{}
    $modsTxt = Join-Path $mods 'mods.txt'
    if (Test-Path $modsTxt) {
        foreach ($line in Get-Content $modsTxt) {
            if ($line -match '^\s*([^;:#\s][^:]*?)\s*:\s*(\d)') { $listed["$($Matches[1])|txt"] = $Matches[2] }
        }
    }
    $modsJson = Join-Path $mods 'mods.json'
    if (Test-Path $modsJson) {
        foreach ($entry in (Get-Content $modsJson -Raw | ConvertFrom-Json)) { $listed["$($entry.mod_name)|json"] = $entry.mod_enabled }
    }
    foreach ($dir in Get-ChildItem $mods -Directory | Where-Object Name -ne 'shared') {
        $flags = @()
        if (Test-Path (Join-Path $dir.FullName 'enabled.txt')) { $flags += 'enabled.txt' }
        if ($listed.ContainsKey("$($dir.Name)|txt")) { $flags += "mods.txt=$($listed["$($dir.Name)|txt"])" }
        if ($listed.ContainsKey("$($dir.Name)|json")) { $flags += "mods.json=$($listed["$($dir.Name)|json"])" }
        $kind = if (Test-Path (Join-Path $dir.FullName 'dlls')) { 'C++' } elseif (Test-Path (Join-Path $dir.FullName 'Scripts')) { 'Lua' } else { '?' }
        Report INFO "  mod $($dir.Name)" ("{0}, {1}" -f $kind, $(if ($flags) { $flags -join ', ' } else { 'not enabled' }))
    }
}

$log = Join-Path $ue4ss 'UE4SS.log'
if (-not (Test-Path $log)) {
    Report WARN 'UE4SS.log' 'not found yet, launch Townfall once with UE4SS installed'
} else {
    Report INFO 'UE4SS.log' ("last written {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Item $log).LastWriteTime)
    try {
        $lines = @(Get-Content $log)
    } catch {
        Report WARN 'UE4SS.log' "could not read ($($_.Exception.Message))"
        $lines = @()
    }
    # Offset dumps like "FArchiveState::ArIsError = 0x29" are not errors.
    $framework = $lines | Where-Object {
        $_ -match "UE4SS - v|Using engine version|\[PS\] Failed|Starting Lua mod|has enabled\.txt|error|fatal|exception" -and
        $_ -notmatch '= 0x[0-9A-F]+$' -and $_ -notmatch '\[TF-'
    }
    $framework | Select-Object -First 40 | ForEach-Object { Write-Host "         $_" }
    $ours = @($lines | Where-Object { $_ -match '\[TF-' })
    if ($ours.Count -gt 40) { Write-Host "         ... $($ours.Count - 40) earlier TF- lines omitted" }
    $ours | Select-Object -Last 40 | ForEach-Object { Write-Host "         $_" }

    if ($lines -match "Starting Lua mod '$ModName'|Mod '$ModName' has enabled\.txt, starting mod") { Report OK "$ModName started by UE4SS" } else { Report WARN "$ModName started by UE4SS" 'not in log yet' }
    if ($lines -match '\[TF-COMPANION\] Townfall Companion loaded') { Report OK 'TF-COMPANION load line' } else { Report WARN 'TF-COMPANION load line' 'not in log yet' }
    if ($lines -match '\[TF-PLAYER\] tracking local pawn') { Report OK 'TF-PLAYER tracking line' } else { Report WARN 'TF-PLAYER tracking line' 'not in log yet' }
}

if ($Install) {
    if ($script:failed) {
        Write-Host "`nNot installing: fix the FAIL items above first." -ForegroundColor Red
        exit 1
    }
    $src = Join-Path $PSScriptRoot "..\$ModName"
    $dst = Join-Path $mods $ModName
    # /IS: copy every file, even one robocopy takes for the same (same size and time).
    robocopy $src $dst /MIR /IS /IT /XD cache tools __pycache__ /XF companion.ini /NJH /NJS /NFL /NDL /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Report FAIL "Installed $ModName" "robocopy failed with exit code $LASTEXITCODE"
        exit 1
    }
    Report OK "Installed $ModName" $dst
}

if ($script:failed) { exit 1 }
