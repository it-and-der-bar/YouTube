<# build-it.ps1 — cleaner rebuild with functions, strict PS syntax, ASCII only #>

param(
  [Parameter(Mandatory=$false)][string]$AppName,
  [Parameter(Mandatory=$false)][string]$RendezvousServer,
  [Parameter(Mandatory=$false)][string]$RsPubKey,
  [switch]$SkipClone,
  [switch]$SkipVcpkgInstall,
  [switch]$NoPack,
  [switch]$NoCleanBuild
)

# =================== helpers ===================
$ErrorActionPreference = 'Stop'
function Say ([string]$m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Ok  ([string]$m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn([string]$m){ Write-Host "[!!] $m" -ForegroundColor Yellow }
function Fail([string]$m){ Write-Host "[XX] $m" -ForegroundColor Red; exit 1 }

function Test-IsAdmin {
    $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
# =================== preflight ===================
function Get-Inputs {
  if (-not $script:AppName)          { $script:AppName          = Read-Host "App-Name (z.B. my-client)" }
  if (-not $script:RendezvousServer) { $script:RendezvousServer = Read-Host "Rendezvous-Server (z.B. rustdesk.example.tld)" }
  if (-not $script:RsPubKey)         { $script:RsPubKey         = Read-Host "RS_PUB_KEY" }

  $script:Root        = (Get-Location).Path
  $script:RustDeskDir = Join-Path $Root 'rustdesk'
  $script:VcpkgDir    = Join-Path $Root 'vcpkg'
  $script:MyResDir    = Join-Path $Root 'my-ressources'
  $script:NsisScript  = Join-Path $Root 'custom-rd-client.nsi'
  $script:Makensis    = 'C:\Program Files (x86)\NSIS\makensis.exe'
  $script:LlvmBin     = 'C:\Program Files\LLVM\bin'

  Ok ("Arbeitsverzeichnis: {0}" -f $Root)
  Ok ("Ziel-Repo: {0}" -f $RustDeskDir)
  Ok ("vcpkg-Ordner: {0}" -f $VcpkgDir)
  Ok ("Ressourcen-Ordner: {0}" -f $MyResDir)

  if (-not (Test-Path $NsisScript)) { Fail ("NSIS-Script fehlt: {0}" -f $NsisScript) } else { Ok ("NSIS-Script: {0}" -f $NsisScript) }
}

#NTFS Check
function Get-FilesystemType {
    param([Parameter(Mandatory)][string]$Path)

    try { $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path }
    catch { $resolved = $Path }

    $driveRoot = [System.IO.Path]::GetPathRoot($resolved)
    if ($driveRoot -match '^[A-Za-z]:\\$') {
        $drv = $driveRoot.Substring(0,1)
        $vol = Get-Volume -DriveLetter $drv -ErrorAction SilentlyContinue
        if ($vol) { return $vol.FileSystem }
        # Fallback (aeltere PS-Versionen)
        $info = (fsutil fsinfo volumeinfo $driveRoot 2>$null)
        if ($info) {
            $line = $info | Select-String 'File System Name'
            if ($line) { return ($line -split ':')[-1].Trim() }
        }
    }
    return 'Unknown'
}

function Ensure-RootOnNtfs {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Root)

    Say "pruefe Dateisystem fuer: $Root"
    if (-not (Test-Path -LiteralPath $Root)) {
        Warn "Pfad existiert nicht: $Root"
        return $false
    }

    $fs = Get-FilesystemType -Path $Root
    if ($fs -eq 'NTFS') {
        Ok "'$Root' liegt auf NTFS."
        return $true
    } else {
        Warn "'$Root' liegt auf $fs (nicht NTFS)."
        return $false
    }
}

# Handling ASLR
function Test-And-Handle-ASLR {
    [CmdletBinding()]
    param(
        [switch]$AutoDisable,
        [switch]$Quiet
    )

    if (-not $Quiet) { Say 'reading system mitigations (ASLR)...' }

    try {
        $sys = Get-ProcessMitigation -System
    } catch {
        Fail 'Cmdlet Get-ProcessMitigation not available. Run PowerShell as Administrator on Windows 10/11.'
    }

    $aslr = $sys.ASLR

    $on = @{}
    foreach ($k in 'BottomUp','HighEntropy','ForceRelocateImages') {
        $val = $aslr.$k
        if     ($val -is [string]) { $on[$k] = ($val -eq 'ON') }
        elseif ($val -is [bool])   { $on[$k] = $val }
        else                       { $on[$k] = $false }
    }

    $anyOn = $on.Values -contains $true

    if (-not $Quiet) {
        Say ("ASLR status: BottomUp={0}  HighEntropy={1}  ForceRelocateImages={2}" -f $aslr.BottomUp,$aslr.HighEntropy,$aslr.ForceRelocateImages)
        if ($anyOn) { Warn 'ASLR ist AN.' }
        else        { Ok 'ASLR ist AUS.'; return 0 }
    } else {
        if (-not $anyOn) { return 0 }
    }

    if ($AutoDisable) {
        if (-not (Test-IsAdmin)) { Fail 'Powershell muss als Admin ausgefuehrt werden.' }
        return (Disable-ASLR -Quiet:$Quiet)
    }

    if ($Quiet) { return 1 }

    $ans = Read-Host 'ASLR deaktivieren (Bei Error: Problem extracting tar - kann deaktieren helfen)? (j/N)'
    if ($ans -match '^(y|j)') {
        if (-not (Test-IsAdmin)) { Fail 'Powershell muss als Admin ausgefuehrt werden.' }
        return (Disable-ASLR -Quiet:$Quiet)
    } else {
        Say 'ASLR bleibt an.'
        return 1
    }
}

function Disable-ASLR {
    [CmdletBinding()]
    param([switch]$Quiet)

    if (-not (Test-IsAdmin)) { Fail 'Powershell muss als Admin ausgefuehrt werden.' }

    if (-not $Quiet) { Say 'ASLR (BottomUp, HighEntropy, ForceRelocateImages) system-weit deaktivieren...' }
    try {
        Set-ProcessMitigation -System -Disable BottomUp,HighEntropy,ForceRelocateImages
    } catch {
        Fail ("Set-ProcessMitigation failed: {0}" -f $_.Exception.Message)
    }
    if (-not $Quiet) { Ok 'ASLR deaktiviert. Neustart notwendig!' }
    return 0
}

function Reset-ASLR-ToDefault {
    [CmdletBinding()]
    param([switch]$Quiet)

    if (-not (Test-IsAdmin)) { Fail 'Powershell muss als Admin ausgefuehrt werden.' }

    if (-not $Quiet) { Say 'resetting ASLR Konfiguration...' }
    try {
        Set-ProcessMitigation -System -Remove -Disable BottomUp,HighEntropy,ForceRelocateImages
    } catch {
        Fail ("Set-ProcessMitigation (reset) fehlgeschlagen: {0}" -f $_.Exception.Message)
    }
    if (-not $Quiet) { Ok 'ASLR auf Standard. Neustart notwendig!' }
    return 0
}

# =================== tools ===================
function Ensure-Git {
  if (Get-Command git -ErrorAction SilentlyContinue) { Ok 'Git gefunden.'; return }
  Warn 'Git fehlt - Installation via winget...'
  winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements | Out-Null
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'Git nicht installiert.' }
  Ok 'Git installiert.'
}

function Find-Vcvars {
  function _probe {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $vc = $null
    if (Test-Path $vswhere) {
      $vc = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -find 'VC\Auxiliary\Build\vcvarsall.bat' 2>$null | Select-Object -First 1
    }
    if (-not $vc) {
      $candidates = @(
        'C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat',
        'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat',
        'C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvarsall.bat',
        'C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat',
        'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat',
        'C:\Program Files (x86)\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat'
      )
      $vc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    return $vc
  }

  $vc = _probe
  if ($vc) {
    $script:Vcvars = $vc
    Ok ("vcvarsall gefunden: {0}" -f $Vcvars)
    return
  }

  Warn 'vcvarsall.bat nicht gefunden (VS 2022 C++ Build Tools fehlen).'
  $answer = Read-Host 'Jetzt die Visual Studio 2022 Build Tools (C++ Toolchain, Win10 SDK, CMake) installieren? (J/N)'
  if ($answer -notin @('J','j','Y','y','Ja','ja','Yes','yes')) {
    Fail 'Abgebrochen. Bitte VS 2022 C++ Build Tools manuell installieren.'
  }

  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail 'winget ist nicht verfuegbar. Bitte winget installieren/aktivieren und erneut versuchen.'
  }

  Say 'Installiere Microsoft.VisualStudio.2022.BuildTools mit erforderlichen Komponenten (dies kann eine Weile dauern)...'
  $override = '--add Microsoft.VisualStudio.Workload.VCTools ' +
              '--add Microsoft.VisualStudio.Component.Windows10SDK.19041 ' +
              '--add Microsoft.VisualStudio.Component.VC.CMake.Project ' +
              '--includeRecommended --passive --norestart'

  $args = @(
    'install',
    '--id','Microsoft.VisualStudio.2022.BuildTools',
    '-e',
    '--source','winget',
    '--accept-package-agreements',
    '--accept-source-agreements',
    '--override', "`"$override`""
  )

  $p = Start-Process -FilePath 'winget' -ArgumentList ($args -join ' ') -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) {
    Fail ("Installation der Build Tools fehlgeschlagen (ExitCode {0})." -f $p.ExitCode)
  }
  Ok 'Build Tools installiert. Suche vcvarsall erneut...'

  $vc = _probe
  if (-not $vc) {
    Fail 'vcvarsall.bat weiterhin nicht gefunden. Starte eine neue PowerShell als Administrator und versuche es erneut.'
  }

  $script:Vcvars = $vc
  Ok ("vcvarsall gefunden: {0}" -f $Vcvars)
}

# erwartet: Say/Ok/Warn/Fail sind bereits definiert
function Ensure-RustToolchain {

    function Test-Cargo {
        $c = Get-Command cargo -ErrorAction SilentlyContinue
        if ($c) { Ok ("cargo gefunden: {0}" -f $c.Source); return $true }
        return $false
    }

    if (Test-Cargo) { return }

    # 1) winget versuchen (mehrere Paket-IDs, user-scope, silent)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "installiere Rust via winget..."
        $ids = @(
            "Rustlang.Rustup",      # rustup bootstrap
            "Rustlang.Rust.MSVC",   # üblicher MSVC-Toolchain
            "Rustlang.Rust.GNU"     # GNU-Toolchain (Fallback)
        )
        foreach ($id in $ids) {
            Say ("winget install --id {0} ..." -f $id)
            try {
                $args = @(
                    "install","--id",$id,"--exact","--source","winget",
                    "--accept-source-agreements","--accept-package-agreements"
                )
                $p = Start-Process -FilePath "winget" -ArgumentList $args -NoNewWindow -PassThru -Wait
                if ($p.ExitCode -eq 0) {
                    # PATH der aktuellen Session ergänzen (rustup legt unter %USERPROFILE%\.cargo\bin ab)
                    $cargoBin = Join-Path $HOME ".cargo\bin"
                    if (Test-Path $cargoBin) { $env:Path = "$cargoBin;$env:Path" }
                    if (Test-Cargo) { return }
                }
            } catch { Warn ("winget fehlgeschlagen für {0}: {1}" -f $id, $_.Exception.Message) }
        }
        Fail "winget konnte Rust nicht bereitstellen."
    } else {
        Fail "winget nicht verfügbar."
    }
}

function Ensure-Perl {

    # Bereits vorhanden?
    $perlCmd = Get-Command perl -ErrorAction SilentlyContinue
    if ($perlCmd) {
        $env:VCPKG_PERL_PATH = $perlCmd.Source
        Write-Host "Perl bereits vorhanden: $($perlCmd.Source)"
        return $perlCmd.Source
    }

    # winget verfuegbar?
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget ist nicht verfuegbar. Bitte zuerst winget installieren/aktivieren."
    }
    Warn("Perl nicht gefunden - installiere Strawberry Perl via winget ...")
    winget install --id StrawberryPerl.StrawberryPerl 

    # Versuch, perl zu finden (PATH kann fuer die aktuelle Session noch nicht aktualisiert sein)
    $perlCmd = Get-Command perl -ErrorAction SilentlyContinue
    if (-not $perlCmd) {
        # Haeufige Installationspfade pruefen und temporaer in PATH aufnehmen
        $candidates = @(
            "$env:LOCALAPPDATA\Programs\Strawberry\perl\bin\perl.exe",
            "C:\Strawberry\perl\bin\perl.exe"
        )
        foreach ($p in $candidates) {
            if (Test-Path $p) {
				$env:Path = ('{0};{1}' -f (Split-Path $p), $env:Path)
                #$env:Path = "$(Split-Path $p);$env:Path"
                $perlCmd = Get-Command perl -ErrorAction SilentlyContinue
                if ($perlCmd) { break }
            }
        }
    }

    if (-not $perlCmd) {
        Fail("Perl wurde nach der Installation nicht gefunden. Bitte Terminal neu oeffnen und erneut versuchen.", 11)
    }

    $env:VCPKG_PERL_PATH = $perlCmd.Source
    Ok ("Perl bereit: $($perlCmd.Source)")
    return $perlCmd.Source
}



function Ensure-LLVM {
  if (Test-Path (Join-Path $LlvmBin 'libclang.dll')) { Ok ("LLVM/libclang: {0}" -f $LlvmBin) }
  else { Fail ("LLVM (libclang.dll) fehlt unter: {0} 
Installer: https://github.com/llvm/llvm-project/releases/download/llvmorg-15.0.2/LLVM-15.0.2-win64.exe" -f $LlvmBin) }
}

function Ensure-SciterDll {
  $script:BinCacheDir   = Join-Path $Root 'bin-cache\win64'
  if (-not (Test-Path $BinCacheDir)) { New-Item -ItemType Directory -Path $BinCacheDir -Force | Out-Null }

  $script:SciterDllCache = Join-Path $BinCacheDir 'sciter.dll'
  if (-not (Test-Path $SciterDllCache)) {
    Say 'Lade sciter.dll in bin-cache...'
    $uri = 'https://raw.githubusercontent.com/c-smile/sciter-sdk/master/bin.win/x64/sciter.dll'
    Invoke-WebRequest -Uri $uri -OutFile $SciterDllCache -UseBasicParsing
    if (-not (Test-Path $SciterDllCache) -or ((Get-Item $SciterDllCache).Length -lt 100000)) {
      Fail 'sciter.dll Download fehlerhaft.'
    }
    Ok ("sciter.dll cached: {0}" -f $SciterDllCache)
  } else {
    Ok ("sciter.dll bereits im Cache: {0}" -f $SciterDllCache)
  }
}

function Ensure-NSIS {
  if (Test-Path $Makensis) { Ok ("makensis gefunden: {0}" -f $Makensis) }
  else { Fail 'NSIS fehlt. Installiere: https://nsis.sourceforge.io/Download' }
}

function Ensure-Vcpkg {
  if (-not (Test-Path $VcpkgDir)) {
    Ensure-Git
    Say ("Klonen vcpkg -> {0}" -f $VcpkgDir)
    git clone https://github.com/microsoft/vcpkg $VcpkgDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'git clone vcpkg fehlgeschlagen.' }
    Ok 'vcpkg geklont.'
  } else { Ok 'vcpkg-Ordner vorhanden.' }

  $exe = Join-Path $VcpkgDir 'vcpkg.exe'
  if (-not (Test-Path $exe)) {
    Say 'Bootstrap vcpkg...'
    & (Join-Path $VcpkgDir 'bootstrap-vcpkg.bat')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $exe)) { Fail 'vcpkg bootstrap fehlgeschlagen.' }
    Ok 'vcpkg gebootstrapped.'
  } else { Ok 'vcpkg.exe vorhanden.' }

  $script:VcpkgExe = $exe
}

# =================== repo/customize ===================
function Clone-RustDesk {
  if (-not (Test-Path $RustDeskDir)) {
    if ($SkipClone) { Fail 'rustdesk/ fehlt aber -SkipClone gesetzt.' }
    Say 'Clone rustdesk Repo...'
    git clone --recurse-submodules https://github.com/rustdesk/rustdesk $RustDeskDir
    if ($LASTEXITCODE -ne 0) { Fail 'git clone rustdesk fehlgeschlagen.' }
    Ok 'rustdesk geklont.'
  } else {
    Ok 'rustdesk/ vorhanden - ueberspringe Clone.'
  }
}

function Patch-Config {
  $cfgDir = Join-Path $RustDeskDir 'libs\hbb_common\src'
  if (-not (Test-Path $cfgDir)) { Fail ("Ordner fehlt: {0}" -f $cfgDir) }
  $cfg = Get-ChildItem -Path $cfgDir -Recurse -File -Filter 'config.rs' | Select-Object -First 1
  if (-not $cfg) { Fail 'config.rs nicht gefunden.' }
  Ok ("config.rs: {0}" -f $cfg.FullName)

  $txt = Get-Content -Raw -LiteralPath $cfg.FullName
  $app = $AppName.Replace('"','\"')
  $rdv = $RendezvousServer.Replace('"','\"')
  $key = $RsPubKey.Replace('"','\"')

  $repApp = "pub static ref APP_NAME: RwLock<String> = RwLock::new(`"$app`".to_owned());"
  $repRdv = "pub const RENDEZVOUS_SERVERS: &[&str] = [`"$rdv`"];"
  $repKey = "pub const RS_PUB_KEY: &str = `"$key`";"

  $txt = [regex]::Replace($txt, '(?m)^*pub\s+static\s+ref\s+APP_NAME:.*$', $repApp)
  $txt = [regex]::Replace($txt,'(?m)^\s*pub\s+const\s+RENDEZVOUS_SERVERS:.*$',"pub const RENDEZVOUS_SERVERS: &[&str] = &[`"$RendezvousServer`"];")
  $txt = [regex]::Replace($txt, '(?m)^\s*pub\s+const\s+RS_PUB_KEY:.*$', $repKey)

  Set-Content -LiteralPath $cfg.FullName -Value $txt -Encoding UTF8
  Ok 'config.rs aktualisiert.'
}


function Patch-NSIS {

    if (-not (Test-Path -LiteralPath $NsisScript)) {
        Fail ("NSIS-Datei nicht gefunden: {0}" -f $NsisScript)
    }

    $app = $AppName.Replace('"','\"') 
    $stem = ($AppName -replace '[^A-Za-z0-9._-]','-') -replace '-{2,}','-'
    $stem = $stem.Trim('-'); if ([string]::IsNullOrWhiteSpace($stem)) { $stem = 'app' }
    $exe  = "$stem.exe"

    $repDefine = '!define APPNAME "' + $app + '"'
    $repOut    = 'OutFile "' + $exe + '"'
    $repFD     = 'VIAddVersionKey "FileDescription" "RustDesk ' + $app + '"'
    $repPN     = 'VIAddVersionKey "ProductName"     "RustDesk"'

    Say ("patch NSIS: {0}" -f $NsisScript)
    $txt = Get-Content -LiteralPath $NsisScript -Raw

    $txt = [regex]::Replace($txt, '(?m)^\s*!define\s+APPNAME\s+.*$',                     $repDefine)
    $txt = [regex]::Replace($txt, '(?m)^\s*OutFile\s+.*$',                               $repOut)
    $txt = [regex]::Replace($txt, '(?m)^\s*VIAddVersionKey\s+"FileDescription"\s+.*$',   $repFD)
    $txt = [regex]::Replace($txt, '(?m)^\s*VIAddVersionKey\s+"ProductName"\s+.*$',       $repPN)

    Set-Content -LiteralPath $NsisScript -Value $txt -Encoding UTF8
    Ok ("NSIS aktualisiert. OutFile -> {0}" -f $exe)
    return $exe
}



function Generate-ResourcesFromLogo {
  if (-not (Test-Path $MyResDir)) { New-Item -ItemType Directory -Path $MyResDir -Force | Out-Null }

  $logo = Join-Path $root 'logo.png'
  if (-not (Test-Path $logo)) { Warn 'logo.png nicht gefunden - Generierung uebersprungen.'; return }

  Add-Type -AssemblyName System.Drawing

  function Resize-SavePng([System.Drawing.Image]$img, [int]$size, [string]$outPath) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size
    try {
      $bmp.SetResolution($img.HorizontalResolution, $img.VerticalResolution)
      $g = [System.Drawing.Graphics]::FromImage($bmp)
      try {
        $g.InterpolationMode  = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.SmoothingMode      = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $g.PixelOffsetMode    = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $g.DrawImage($img, 0, 0, $size, $size)
      } finally { $g.Dispose() }
      $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $bmp.Dispose() }
  }

  function New-IcoFromPngs([string]$outPath, [string[]]$pngPaths) {
    # schreibt eine .ico Datei mit PNG-kodierten Eintraegen (Vista+)
    $streams = @()
    try {
      foreach ($p in $pngPaths) {
        $ms = New-Object System.IO.MemoryStream
        [byte[]]$bytes = [System.IO.File]::ReadAllBytes($p)
        $ms.Write($bytes, 0, $bytes.Length) | Out-Null
        $streams += ,@($p, $ms, $bytes.Length)
      }

      $fs = [System.IO.File]::Open($outPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
      try {
        $bw = New-Object System.IO.BinaryWriter($fs)

        $count = $streams.Count
        # ICONDIR
        $bw.Write([UInt16]0)      # Reserved
        $bw.Write([UInt16]1)      # Type = 1 (icon)
        $bw.Write([UInt16]$count) # Count

        # Platzhalter fuer ICONDIRENTRYs merken
        $entriesPos = $fs.Position
        for ($i=0; $i -lt $count; $i++) {
          # 16 Bytes pro Eintrag
          $bw.Write([byte]0)      # width placeholder
          $bw.Write([byte]0)      # height placeholder
          $bw.Write([byte]0)      # colors (0)
          $bw.Write([byte]0)      # reserved
          $bw.Write([UInt16]1)    # planes
          $bw.Write([UInt16]32)   # bitcount
          $bw.Write([UInt32]0)    # bytes in res placeholder
          $bw.Write([UInt32]0)    # offset placeholder
        }

        $entryData = @()
        foreach ($s in $streams) {
          $pngPath = $s[0]; $ms = $s[1]; $len = [UInt32]$s[2]
          $offset = [UInt32]$fs.Position
          $ms.Position = 0
          $ms.CopyTo($fs)
          $entryData += ,@($pngPath, $len, $offset)
        }

        $fs.Position = $entriesPos
        foreach ($e in $entryData) {
          $name = [System.IO.Path]::GetFileNameWithoutExtension($e[0])
          if ($name -match '(\d+)x(\d+)') { $w=[int]$matches[1]; $h=[int]$matches[2] } else { $w=256; $h=256 }
          $byteW = if ($w -ge 256) { 0 } else { [byte]$w }
          $byteH = if ($h -ge 256) { 0 } else { [byte]$h }

          $bw.Write([byte]$byteW)
          $bw.Write([byte]$byteH)
          $bw.Write([byte]0)            # colors
          $bw.Write([byte]0)            # reserved
          $bw.Write([UInt16]1)          # planes
          $bw.Write([UInt16]32)         # bitcount
          $bw.Write([UInt32]$e[1])      # bytesInRes
          $bw.Write([UInt32]$e[2])      # offset
        }

      } finally { $fs.Dispose() }
    } finally {
      foreach ($s in $streams) { $s[1].Dispose() }
    }
  }

  $img = [System.Drawing.Image]::FromFile($logo)
  try {
    $p32   = Join-Path $MyResDir '32x32.png'
    $p64   = Join-Path $MyResDir '64x64.png'
    $p128  = Join-Path $MyResDir '128x128.png'
    $p256  = Join-Path $MyResDir '128x128@2x.png' 
    $pIcon = Join-Path $MyResDir 'icon.png'      

    Resize-SavePng $img 32  $p32
    Resize-SavePng $img 64  $p64
    Resize-SavePng $img 128 $p128
    Resize-SavePng $img 256 $p256
    Resize-SavePng $img 256 $pIcon

    # icon.ico mit 16/32/48/64/128
    $tmpDir = Join-Path $MyResDir '_tmp_ico'
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    try {
      $p16  = Join-Path $tmpDir '16x16.png'
      $p24  = Join-Path $tmpDir '24x24.png'
      $p48  = Join-Path $tmpDir '48x48.png'
      Resize-SavePng $img 16  $p16
      Resize-SavePng $img 24  $p24
      Resize-SavePng $img 48  $p48

      $iconIco = Join-Path $MyResDir 'icon.ico'
      $trayIco = Join-Path $MyResDir 'tray-icon.ico'

      New-IcoFromPngs $iconIco @($p16,$p24,$p32,$p48,$p64,$p128,$p256)
      New-IcoFromPngs $trayIco @($p16,$p24,$p32)
    } finally {
      Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  finally { $img.Dispose() }

  Ok 'Ressourcen aus logo.png generiert (PNG + ICO).'
}


function Patch-IconBase64 {
  $png = Join-Path $MyResDir '32x32.png'
  if (-not (Test-Path $png)) { return }

  $uiRs = Join-Path $RustDeskDir 'src\ui.rs'
  if (-not (Test-Path $uiRs)) { return }

  $b = [IO.File]::ReadAllBytes($png)
  if (-not $b -or $b.Count -lt 64) { return }
  $b64 = [Convert]::ToBase64String($b)

  $txt = Get-Content -Raw -LiteralPath $uiRs

  $pattern = '(?s)(?<=["''])[dD]ata:image/png;base64,[A-Za-z0-9+/=]+(?=["''])'
  $new    = 'data:image/png;base64,' + $b64
  $txt    = [regex]::Replace($txt, $pattern, $new)

  Set-Content -LiteralPath $uiRs -Value $txt -Encoding UTF8
  Ok "src\\ui.rs: Icons durch Base64 aus 32x32.png ersetzt."
}

function Copy-Resources {
  $dst = Join-Path $RustDeskDir 'res'
  if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst | Out-Null }
  if (Test-Path $MyResDir) {
    Copy-Item -Path (Join-Path $MyResDir '*') -Destination $dst -Recurse -Force
    Ok 'Ressourcen kopiert.'
  } else {
    Warn 'my-ressources nicht gefunden - res-Kopie uebersprungen.'
  }
}

function Set-Icon {
  $src = Join-Path $MyResDir 'icon.ico'
  $dst = Join-Path $RustDeskDir 'flutter\windows\runner\resources\app_icon.ico'
  $dir = Split-Path $dst -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if (Test-Path $src) { Copy-Item $src $dst -Force; Ok ("Icon gesetzt: {0}" -f $dst) } else { Warn 'icon.ico nicht gefunden - Icon unveraendert.' }
}

# =================== build (cmd script) ===================
function Write-CmdScript {
  if (-not (Test-Path $Vcvars)) { Fail 'vcvarsall.bat nicht gefunden - CMD-Build wird nicht erzeugt.' }

  $cmd = New-Object System.Collections.Generic.List[string]
  $cmd.Add('@echo off')
  $cmd.Add('setlocal')
  $cmd.Add(("cd /d ""{0}""" -f $Root))
  $cmd.Add(('echo [..] Using vcvars: "{0}"' -f $Vcvars))
  $cmd.Add(('if not exist "{0}" (' -f $Vcvars))
  $cmd.Add(('  echo [XX] vcvarsall.bat not found at "{0}"' -f $Vcvars))
  $cmd.Add('  exit /b 1')
  $cmd.Add(')')
  $cmd.Add(('call "{0}" amd64' -f $Vcvars))
  $cmd.Add('if errorlevel 1 exit /b 1')
  $cmd.Add('echo [OK] vcvars environment loaded.')
  $cmd.Add(('set "VCPKG_ROOT={0}"' -f $VcpkgDir))
  $cmd.Add('set "VCPKG_DEFAULT_TRIPLET=x64-windows-static"')
  $cmd.Add('echo [OK] VCPKG_ROOT=%VCPKG_ROOT%, TRIPLET=%VCPKG_DEFAULT_TRIPLET%')

  if ($SkipVcpkgInstall) {
    $cmd.Add('echo [..] Skip vcpkg install (per parameter).')
  } else {
    $cmd.Add('if exist "%VCPKG_ROOT%\installed\%VCPKG_DEFAULT_TRIPLET%\include\opus\opus_multistream.h" (')
    $cmd.Add('  echo [OK] opus headers present in vcpkg.')
    $cmd.Add(') else (')
    $cmd.Add('  echo [..] vcpkg install (libvpx, libyuv, opus, aom)')
    $cmd.Add('  "%VCPKG_ROOT%\vcpkg.exe" install libvpx:%VCPKG_DEFAULT_TRIPLET% libyuv:%VCPKG_DEFAULT_TRIPLET% opus:%VCPKG_DEFAULT_TRIPLET% aom:%VCPKG_DEFAULT_TRIPLET%')
    $cmd.Add('  if errorlevel 1 exit /b 1')
    $cmd.Add('  echo [OK] vcpkg install done.')
    $cmd.Add(')')
  }

  $cmd.Add(('set "LIBCLANG_PATH={0}"' -f $LlvmBin))
  $cmd.Add('echo [OK] LIBCLANG_PATH=%LIBCLANG_PATH%')

  $cmd.Add(("cd /d ""{0}""" -f $RustDeskDir))

  if (-not $NoCleanBuild) {
    $cmd.Add('echo [..] cargo clean')
    $cmd.Add('cargo clean')
  } else {
    $cmd.Add('echo [..] skip cargo clean (NoCleanBuild)')
  }

  $cmd.Add('if not exist "target\debug"   mkdir target\debug')
  $cmd.Add('if not exist "target\release" mkdir target\release')
  
  $cmd.Add('echo [..] Use cached sciter.dll')
  $cmd.Add(("copy /y ""{0}"" target\debug\sciter.dll   >nul" -f $SciterDllCache))
  $cmd.Add(("copy /y ""{0}"" target\release\sciter.dll >nul" -f $SciterDllCache))
  
  $cmd.Add('set RUSTFLAGS=-C target-feature=+crt-static')
  $cmd.Add('echo [..] cargo build --release')
  $cmd.Add('cargo build --release')
  $cmd.Add('if errorlevel 1 exit /b 1')
  $cmd.Add('echo [OK] cargo build finished.')

  $cmd.Add(("cd /d ""{0}""" -f $RustDeskDir))
  $cmd.Add('echo [..] Prepare NSIS package layout')
  $cmd.Add('rmdir /s /q pkg 2>nul')
  $cmd.Add('mkdir pkg')
  $cmd.Add('copy /y target\release\rustdesk.exe pkg\ >nul')
  $cmd.Add('copy /y target\release\sciter.dll   pkg\ >nul')
  $cmd.Add('mkdir pkg\src')
  $cmd.Add('xcopy /e /i /y src\ui pkg\src\ui >nul')
  $cmd.Add('echo [OK] Package tree ready (pkg\...).')

  if ($NoPack) {
    $cmd.Add('echo [..] NSIS pack skipped.')
  } else {
    $cmd.Add(("echo [..] NSIS pack... '{0}' '{1}'" -f $Makensis,$NsisScript))
    $cmd.Add((' "{0}" "{1}"' -f $Makensis,$NsisScript))
  }

  $cmd.Add('echo [OK] CMD stage completed.')
  $cmd.Add('endlocal')

  $script:CmdFile = Join-Path $Root 'build-run.cmd'
  Set-Content -Path $CmdFile -Value $cmd -Encoding ASCII
  Ok ("CMD-Skript geschrieben: {0}" -f $CmdFile)
}

function Run-CmdScript {
  Say 'Starte Build (CMD via vcvarsall)...'
  & cmd.exe /c "`"$CmdFile`""
  if ($LASTEXITCODE -ne 0) { Fail 'Build fehlgeschlagen.' }
  Ok 'FERTIG. Wenn nicht -NoPack: NSIS-Ausgabe erstellt.'
}

# =================== main ===================
Get-Inputs
Ensure-RootOnNtfs -Root $Root
Test-And-Handle-ASLR
Ensure-NSIS
Ensure-LLVM
#Ensure-Perl
Ensure-RustToolchain
Find-Vcvars
Ensure-Vcpkg
Clone-RustDesk
Patch-Config
Patch-NSIS
Generate-ResourcesFromLogo
Patch-IconBase64
Copy-Resources
Set-Icon
Ensure-SciterDll
Write-CmdScript
Run-CmdScript
