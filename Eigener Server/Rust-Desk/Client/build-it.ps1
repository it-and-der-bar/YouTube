<# build-it.ps1 — cleaner rebuild with functions, strict PS syntax, ASCII only #>

param(
  [Parameter(Mandatory=$false)][string]$AppName,
  [alias("Server")][Parameter(Mandatory=$false)][string]$RendezvousServer,
  [alias("PublicKey", "Key")][Parameter(Mandatory=$false)][string]$RsPubKey,
  [alias("Version")][Parameter(Mandatory=$false)][string]$RsVersion,
  [switch]$SkipClone,
  [switch]$SkipVcpkgInstall,
  [switch]$NoCleanBuild,
  [switch]$PatchSecureTcp,
  [switch]$RemoveNewVersionInfo,
  [switch]$Uninstall
)

# =================== helpers ===================
$ErrorActionPreference = 'Stop'
function Say ([string]$m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Ok  ([string]$m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn([string]$m){ Write-Host "[!!] $m" -ForegroundColor Yellow }
function Fail([string]$m){ Write-Host "[XX] $m" -ForegroundColor Red; exit 1 }

$defaultProgressPreference = $ProgressPreference
$ProgressPreference = 'SilentlyContinue'
$Root = (Get-Location).Path

function Test-IsAdmin {
    $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# =================== preflight ===================
function Get-Inputs {
  if (-not $script:AppName)          { $script:AppName          = Read-Host "App-Name (z.B. my-client)" }
  if (-not $script:RendezvousServer) { $script:RendezvousServer = Read-Host "Rendezvous-Server (z.B. rustdesk.example.tld)" }
  if (-not $script:RsPubKey)         { $script:RsPubKey         = Read-Host "RS_PUB_KEY" }
  if (-not $script:AppName)          { $script:AppName          = "RustDesk" }
  if (-not $script:RsVersion)        { $script:RsVersion        = "master" } #1.4.4 / latest / master

  $script:RustDeskDir   = Join-Path $Root 'rustdesk'
  $script:RustBridgeDir = Join-Path $Root 'rust-bridge'
  $script:VcpkgDir      = Join-Path $Root 'vcpkg'
  $script:FlutterDir    = Join-Path $Root 'flutter'
  $script:MyResDir      = Join-Path $Root 'my-ressources'
  $script:LlvmBin       = 'C:\Program Files\LLVM\bin'
  $script:CacheDir      = Join-Path $Root 'cache'

  $script:FLUTTER_VERSION             = "3.24.5"
  $script:CARGO_EXPAND_VERSION        = "1.0.95"
  $script:LLVM_VERSION                = "15.0.6"
  $script:FLUTTER_RUST_BRIDGE_VERSION = "1.80.1"
  $script:RUSTUP_VERSION              = "1.75"
  $script:RD_TOPMOSTWINDOW_COMMIT_ID  = "53b548a5398624f7149a382000397993542ad796"
  $script:VCPKG_COMMIT_ID             = "120deac3062162151622ca4860575a33844ba10b"
  $script:VCPKG_BINARY_SOURCES        = "clear;x-gha,readwrite"
  
  $Env:VCPKG_ROOT                     = $VcpkgDir
  $Env:VCPKG_DEFAULT_HOST_TRIPLET     = "x64-windows-static"
  $Env:VCPKG_DISABLE_METRICS          = "true"
  $Env:VCPKG_BINARY_SOURCES           = "clear;x-gha,readwrite"

  Ok ("AppName: {0}" -f $AppName)
  Ok ("Arbeitsverzeichnis: {0}" -f $Root)
  Ok ("Ziel-Repo: {0}" -f $RustDeskDir)
  Ok ("vcpkg-Ordner: {0}" -f $VcpkgDir)
  Ok ("Ressourcen-Ordner: {0}" -f $MyResDir)
  
  if (-not (Test-Path $CacheDir)) { New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null }

  #Unzip faster than Expand-Archive
  Add-Type -Assembly "System.IO.Compression.Filesystem"
}

function ReplaceInFile {
  param (
    [string]$File,
    [string]$pattern,
    [string]$NewString,
    [switch]$UseRegex
  )
    if (Test-Path $File) {
      if ($UseRegex) {
        (Get-Content -Raw -LiteralPath $File) -replace "$pattern", "$NewString" | Set-Content -Path "$File" -Encoding UTF8
      } else {
        (Get-Content -Raw -LiteralPath $File).Replace("$pattern", "$NewString") | Set-Content -Path "$File" -Encoding UTF8
      }
    }
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
    Say "pruefe Dateisystem fuer: $Root"
    if (-not (Test-Path -LiteralPath $Root)) {
        Warn "Pfad existiert nicht: $Root"
        return
    }

    $fs = Get-FilesystemType -Path $Root
    if ($fs -eq 'NTFS') {
        Ok "'$Root' liegt auf NTFS."
    } else {
        Warn "'$Root' liegt auf $fs (nicht NTFS)."
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
        else        { Ok 'ASLR ist AUS.'; return }
    } else {
        if (-not $anyOn) { return }
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
        return
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
    return
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
    return
}

# =================== tools ===================
function Ensure-Git {
  if (Get-Command git -ErrorAction SilentlyContinue) { Ok 'Git gefunden.'; return }
  Warn 'Git fehlt - Installation via winget...'
  winget install --id Git.Git -e --source winget --silent --accept-source-agreements --accept-package-agreements | Out-Null
  if (Test-Path "C:\Program Files\Git\bin") { $env:Path = "C:\Program Files\Git\bin;$env:Path" }
  if (Test-Path "C:\Program Files\Git\usr\bin") { $env:Path = "C:\Program Files\Git\usr\bin;$env:Path" }
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
  while ((Get-Process -Name setup -ErrorAction SilentlyContinue).MainWindowTitle -eq "Visual Studio Installer") {
    Start-Sleep 2
  }
  Ok 'Build Tools installiert. Suche vcvarsall erneut...'

  $vc = _probe
  if (-not $vc) {
    Fail 'vcvarsall.bat weiterhin nicht gefunden. Starte eine neue PowerShell als Administrator und versuche es erneut.'
  }

  $script:Vcvars = $vc
  Ok ("vcvarsall gefunden: {0}" -f $Vcvars)
}

function Find-MSBuildTools {
  $MSBuildTools = Resolve-Path "$(Resolve-Path "$Vcvars\..\..\..\.." -ErrorAction SilentlyContinue)\MSBuild\Current\Bin\amd64\" -ErrorAction SilentlyContinue
  if ($MSBuildTools) {
    Ok ("MSBuildTools gefunden: {0}" -f $MSBuildTools)
    $env:Path = "$MSBuildTools;$env:Path"
  } else { Warn "MSBuild Tools nicht gefunden"}
}

function Ensure-RustToolchain {

    function Test-Cargo {
        $c = Get-Command cargo -ErrorAction SilentlyContinue
        if ($c) { Ok ("cargo gefunden: {0}" -f $c.Source); return $true }
        return $false
    }

    if (Test-Cargo) { 
      if ((rustc -V) -like "*$($RUSTUP_VERSION)*") { return }
    }

    # 1) winget versuchen (mehrere Paket-IDs, user-scope, silent)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "installiere Rust via winget..."
        $ids = @(
            "Rustlang.Rustup",      # rustup bootstrap
            "Rustlang.Rust.MSVC",   # ueblicher MSVC-Toolchain
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
                    # PATH der aktuellen Session ergaenzen (rustup legt unter %USERPROFILE%\.cargo\bin ab)
                    $cargoBin = Join-Path $HOME ".cargo\bin"
                    if (Test-Path $cargoBin) { $env:Path = "$cargoBin;$env:Path" }
                    if ((rustc -V) -notlike "*$($RUSTUP_VERSION)*") {
                        Say ("Pin Rust Toolchain ({0})" -f $RUSTUP_VERSION)
                        rustup install --no-self-update --allow-downgrade $RUSTUP_VERSION
                        rustup default $RUSTUP_VERSION
                    }
                    if (Test-Cargo) { return }
                }
            } catch { Warn ("winget fehlgeschlagen fuer {0}: {1}" -f $id, $_.Exception.Message) }
        }
        Fail "winget konnte Rust nicht bereitstellen."
    } else {
        Fail "winget nicht verfuegbar."
    }
}

function Ensure-Python {
  if ((Get-Command python) -and (Get-Command python).Version.Major -ne 0) {
    if (((python --version) -match "Python 3") -and (Get-Command python3).Version.Major -ne 0) {
      Ok ("Python 3: {0}" -f (Get-Command python).Source)
      return
    }
  }

  if (!(Get-Command python)) {
    Warn 'Python3 fehlt - Installation via winget...'
    winget install --id Python.Python.3.13 -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Ok ("Python3 installiert: {0}" -f (Get-Command python).Source)
  }

  if (!(Get-Command python3) -or (Get-Command python3).Version.Major -eq 0) {
    Say "Erstelle Symbolic Link python3 -> python"
    $pSource = (Get-Command python).Source
    New-Item -ItemType SymbolicLink -Path $pSource.Replace("python.exe","python3.exe") -Target $pSource
  }
}

function Ensure-LLVM {
  if (Test-Path (Join-Path $LlvmBin 'libclang.dll')) {
    if ((clang --version) -match "$LLVM_VERSION") {
      Ok ("LLVM/libclang: {0}" -f $LlvmBin)
      return
    }
  }

  Warn "LLVM ($LLVM_VERSION) fehlt - Installation via winget..."
  winget install --id LLVM.LLVM -e --version $LLVM_VERSION --silent --accept-source-agreements --accept-package-agreements | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail 'LLVM Installation fehlgeschlagen.' }
  $env:Path = "$LlvmBin;$env:Path"
  Ok "LLVM installiert"
}

function Ensure-SciterDll {
  $script:SciterDllCache = Join-Path $CacheDir 'sciter.dll'
  if (-not (Test-Path $SciterDllCache)) {
    Say "Lade sciter.dll in $CacheDir..."
    $uri = 'https://raw.githubusercontent.com/c-smile/sciter-sdk/master/bin.win/x64/sciter.dll'
    $defaultProgressPreference = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $uri -OutFile $SciterDllCache -UseBasicParsing
    $ProgressPreference = $defaultProgressPreference
    if (-not (Test-Path $SciterDllCache) -or ((Get-Item $SciterDllCache).Length -lt 100000)) {
      Fail 'sciter.dll Download fehlerhaft.'
    }
    Ok ("sciter.dll cached: {0}" -f $SciterDllCache)
  } else {
    Ok ("sciter.dll bereits im Cache: {0}" -f $SciterDllCache)
  }
}

function Ensure-Vcpkg {
  if (-not (Test-Path $VcpkgDir)) {
    Ensure-Git
    Say ("Klonen vcpkg -> {0}" -f $VcpkgDir)
    git clone --revision=$VCPKG_COMMIT_ID "https://github.com/microsoft/vcpkg" $VcpkgDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'git clone vcpkg fehlgeschlagen.' }
    Ok 'vcpkg geklont.'
  } else { Ok 'vcpkg-Ordner vorhanden.' }

  $exe = Join-Path $VcpkgDir 'vcpkg.exe'
  if (-not (Test-Path $exe)) {
    Say 'Bootstrap vcpkg...'
    & (Join-Path $VcpkgDir 'bootstrap-vcpkg.bat') -disableMetrics
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $exe)) { Fail 'vcpkg bootstrap fehlgeschlagen.' }
    Ok 'vcpkg gebootstrapped.'
  } else { Ok 'vcpkg.exe vorhanden.' }
  $script:VcpkgExe = $exe
}

function Ensure-Flutter {
  $script:FlutterCache = Join-Path $CacheDir 'flutter.zip'
  if (-not (Test-Path $FlutterCache)) {
    Say "Lade Flutter Framework nach $CacheDir..."
    $uri = "https://storage.googleapis.com/flutter_infra_release/releases/stable/windows/flutter_windows_${FLUTTER_VERSION}-stable.zip"
    $defaultProgressPreference = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri "$uri" -OutFile "$FlutterCache" -UseBasicParsing
    $ProgressPreference = $defaultProgressPreference
    if (-not (Test-Path $FlutterCache)) {
      Fail 'Flutter Download fehlerhaft.'
    }
    Ok ("Flutter cached: {0}" -f $FlutterCache)
  } else { Ok 'Flutter-Cache vorhanden.' }
  
  if ((Test-Path "$FlutterDir") -and $NoCleanBuild) { 
    Ok 'Flutter-Ordner vorhanden.'
  } else {
    if ((Test-Path $FlutterDir)) {
      Remove-Item $FlutterDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Say "Entpacke..."
    [System.IO.Compression.ZipFile]::ExtractToDirectory("$FlutterCache", "$Root")
  }
  
  $env:Path = ('{0};{1}' -f (Join-Path $FlutterDir bin), $env:Path)
  
  Say "bootstrap flutter..."
  flutter doctor -v
  flutter precache --windows
  if ($LASTEXITCODE -ne 0) { Pop-Location; Fail 'flutter bootstrap fehlgeschlagen.' }
  Ok 'bootstrap gebootstrapped.'

  Say "Ersetze Flutter Engine mit RustDesk Engine"
  $EngingeZip = Join-Path $CacheDir "windows-x64-release.zip"
  if (!(Test-Path $EngingeZip)) {
      Say "Lade Engine nach $CacheDir..."
      Invoke-WebRequest -Uri "https://github.com/rustdesk/engine/releases/download/main/windows-x64-release.zip" -OutFile $EngingeZip
      Ok "Engine cached: $EngingeZip"
  }
  $TempEnginePath = Join-Path $FlutterDir "windows-x64-release"
  if ((Test-Path $TempEnginePath)) {
      Remove-Item $TempEnginePath -Recurse -Force -ErrorAction SilentlyContinue | Out-Null
  }
  Say "Entpacke..."
  [System.IO.Compression.ZipFile]::ExtractToDirectory($EngingeZip, $TempEnginePath)
  Move-Item -Force $TempEnginePath\*  (Join-Path $FlutterDir "bin\cache\artifacts\engine\windows-x64-release")
  Remove-Item $TempEnginePath -Recurse -Force -ErrorAction SilentlyContinue | Out-Null
}

function Ensure-usbmmidd {
  $script:UsbmmiddCache = Join-Path $CacheDir 'usbmmidd_v2.zip'
  if (!(Test-Path $UsbmmiddCache)) {
      Say "Lade usbmmidd_v2..."
      Invoke-WebRequest -Uri "https://github.com/rustdesk-org/rdev/releases/download/usbmmidd_v2/usbmmidd_v2.zip" -OutFile $UsbmmiddCache
      if (Test-Path $UsbmmiddCache) {Ok "usbmmidd_v2 cached: $UsbmmiddCache"} else {Fail "herunterladen fehlgeschlagen."}
  } else { Ok 'usbmmidd_v2 cache vorhanden.' }
}

function Ensure-ImageMagick {
  winget list -e --id ImageMagick.Q16-HDRI | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Say "installiere ImageMagick.Q16-HDRI via winget..."
    winget install --id ImageMagick.Q16-HDRI -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
  } else { Ok ("ImageMagick vorhanden: {0}" -f (Get-Command magick).Source) }
}

function Generate-RustBridge {
  if ((Test-Path $RustBridgeDir) -and -not $NoCleanBuild) {
    Say "Bereinige $RustBridgeDir"
    Remove-Item $RustBridgeDir -Recurse -Force -ErrorAction SilentlyContinue
  }

  if (Test-Path (Join-Path $RustDeskDir "flutter\lib\generated_bridge.dart")) {
    Ok 'generated_bridge.dart vorhanden - ueberspringe Generierung.'
    return
  }

  if (-not (Test-Path $RustBridgeDir)) {
    Say 'Clone rustdesk Ordner...'
    Copy-Item $RustDeskDir $RustBridgeDir -Recurse
  }

  Say "bootstrap rust-bridge..."
  Set-Location $RustBridgeDir
  cargo install cargo-expand --version $CARGO_EXPAND_VERSION --locked
  cargo install flutter_rust_bridge_codegen --version $FLUTTER_RUST_BRIDGE_VERSION --features "uuid" --locked
  Push-Location flutter
  ReplaceInFile -File pubspec.yaml -pattern 'extended_text: 14.0.0' -NewString 'extended_text: 13.0.0'
  flutter pub get
  Pop-Location

  Say "Generiere flutter rust bridge" -ForegroundColor green
  & "$env:UserProfile\.cargo\bin\flutter_rust_bridge_codegen.exe" --rust-input .\src\flutter_ffi.rs --dart-output .\flutter\lib\generated_bridge.dart --c-output .\flutter\macos\Runner\bridge_generated.h
  #Copy-Item "flutter\macos\Runner\bridge_generated.h" "flutter\ios\Runner\bridge_generated.h"
  Set-Location $Root
  if (Test-Path (Join-Path $RustBridgeDir "flutter\lib\generated_bridge.dart")) {
    Copy-Item (Join-Path $RustBridgeDir "src\bridge_generated.rs") (Join-Path $RustDeskDir "src")
    Copy-Item (Join-Path $RustBridgeDir "src\bridge_generated.io.rs") (Join-Path $RustDeskDir "src")
    Copy-Item (Join-Path $RustBridgeDir "flutter\lib\generated_bridge.dart") (Join-Path $RustDeskDir "flutter\lib")
    Copy-Item (Join-Path $RustBridgeDir "flutter\lib\generated_bridge.freezed.dart") (Join-Path $RustDeskDir "flutter\lib")
    #Copy-Item (Join-Path $RustBridgeDir "flutter\macos\Runner\bridge_generated.h") (Join-Path $RustDeskDir "flutter\macos\Runner")
    #Copy-Item (Join-Path $RustBridgeDir "flutter\ios\Runner\bridge_generated.h") (Join-Path $RustDeskDir "flutter\macos\Runner")
    Ok 'rust-bridge erstellt'
  } else {
    Fail 'Generierung rust-bridge fehlgeschlagen.'
  }
}

function Generate-RustDeskTempTopMostWindow {
  $RustDeskTempTopMostWindow = Join-Path $Root "RustDeskTempTopMostWindow"
  $script:WindowInjectionDLL = Join-Path $RustDeskTempTopMostWindow "WindowInjection\x64\Release\WindowInjection.dll"
  $RustDeskTempTopMostWindowCache = Join-Path $CacheDir "RustDeskTempTopMostWindow.zip"

  if ((Test-Path $RustDeskTempTopMostWindow) -and (Test-Path $WindowInjectionDLL) -and $NoCleanBuild) {
    Ok 'RustDeskTempTopMostWindow/ vorhanden - ueberspringe Erstellen.'
    return
  } else {
    if (Test-Path $RustDeskTempTopMostWindow) {
      Say "Bereinige $RustDeskTempTopMostWindow"
      Remove-Item $RustDeskTempTopMostWindow -Recurse -Force -ErrorAction SilentlyContinue
    }
  }

  Say ("Erstelle RustDeskTempTopMostWindow (WindowInjection.dll)")
  if (Test-Path $RustDeskTempTopMostWindowCache) {
      if (Test-Path $RustDeskTempTopMostWindow) {
          Remove-Item $RustDeskTempTopMostWindow -Recurse -Force -ErrorAction SilentlyContinue
      }
      [System.IO.Compression.ZipFile]::ExtractToDirectory($RustDeskTempTopMostWindowCache, $RustDeskTempTopMostWindow)
  } else {
      if (Test-Path $RustDeskTempTopMostWindow) {
          Remove-Item $RustDeskTempTopMostWindow -Recurse -Force -ErrorAction SilentlyContinue
      }

      Say ("Klonen RustDeskTempTopMostWindow: {0}" -f $RustDeskTempTopMostWindow)
      git clone "https://github.com/rustdesk-org/RustDeskTempTopMostWindow" $RustDeskTempTopMostWindow

      Write-Host "Generate cache" -ForegroundColor green
      Push-Location $RustDeskTempTopMostWindow
      Compress-Archive -Path * -DestinationPath $RustDeskTempTopMostWindowCache -Force -CompressionLevel Fastest
      Pop-Location
  }

  Write-Output "Generiere WindowInjection.dll"
  Push-Location $RustDeskTempTopMostWindow
  git checkout $RD_TOPMOSTWINDOW_COMMIT_ID
  
  Say "Migriere PlatformToolset v142 zu v143"
  $patchPlatform = Join-Path $RustDeskTempTopMostWindow "WindowInjection\WindowInjection.vcxproj"
  ReplaceInFile -File $patchPlatform -pattern "<PlatformToolset>v142</PlatformToolset>" -NewString "<PlatformToolset>v143</PlatformToolset>"
  Ok ""
  
  msbuild WindowInjection\WindowInjection.vcxproj -p:Configuration=Release -p:Platform=x64 -p:TargetVersion=Windows10
  Pop-Location
  if (Test-Path $WindowInjectionDLL) {
    Ok ('WindowInjection.dll vorhanden.')
  } else {
    Fail ("Build fehlgeschlagen.")
  }
}

# =================== repo/customize ===================
function Clone-RustDesk {
  if ((Test-Path $RustDeskDir) -and -not $NoCleanBuild) {
    Say "Bereinige $RustDeskDir"
    Remove-Item $RustDeskDir -Recurse -Force -ErrorAction SilentlyContinue
  }
  if (-not (Test-Path $RustDeskDir)) {
    if ($SkipClone) { Fail 'rustdesk/ fehlt aber -SkipClone gesetzt.' }
    Say 'Clone rustdesk Repo...'
    git clone --recurse-submodules --branch $RsVersion "https://github.com/rustdesk/rustdesk" $RustDeskDir
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
  $repRdv = "pub const RENDEZVOUS_SERVERS: &[&str] = &[`"$rdv`"];"
  $repKey = "pub const RS_PUB_KEY: &str = `"$key`";"

  $txt = [regex]::Replace($txt, '(?m)^*pub\s+static\s+ref\s+APP_NAME:.*$', $repApp)
  $txt = [regex]::Replace($txt,'(?m)^\s*pub\s+const\s+RENDEZVOUS_SERVERS:.*$',$repRdv)
  $txt = [regex]::Replace($txt, '(?m)^\s*pub\s+const\s+RS_PUB_KEY:.*$', $repKey)

  Set-Content -LiteralPath $cfg.FullName -Value $txt -Encoding UTF8
  Ok 'config.rs aktualisiert.'
}

function Patch-Client {
  Say "Entferne Server Hinweis"
  ReplaceInFile -File (Join-Path $RustDeskDir "flutter\lib\desktop\pages\connection_page.dart") -pattern "if (!isIncomingOnly) setupServerWidget()," -NewString "//if (!isIncomingOnly) setupServerWidget(),"
  Ok ""

  if ($PatchSecureTcp) {
    # https://github.com/infiniteremote/installer/issues/36
    Say "Patch Secure TCP Verbindungsfehler bei eigenem API-Server"
    ReplaceInFile -File (Join-Path $RustDeskDir "\src\client.rs") -pattern '(peer, "", key, token)' -NewString '(peer, "", key, "")'
    Ok ""
  }

  if ($RemoveNewVersionInfo) {
    Say "Entferne neue Version Benachrichtigung"
    ReplaceInFile -File (Join-Path $RustDeskDir "flutter\lib\desktop\pages\desktop_home_page.dart") -pattern 'updateUrl.isNotEmpty' -NewString 'false'
    ReplaceInFile -File (Join-Path $RustDeskDir "src\common.rs") -UseRegex -pattern "let \(request, url\) =([\r\n]|.)*?Ok\(\(\)\)" -NewString "Ok(())"
    Ok ""
  }
}

function Generate-ResourcesFromLogo {
  if (-not (Test-Path $MyResDir)) { New-Item -ItemType Directory -Path $MyResDir -Force | Out-Null }

  $logo = Join-Path $root 'logo.png'
  if (-not (Test-Path $logo)) { Warn 'logo.png nicht gefunden - Generierung uebersprungen.'; return }

    magick $logo (Join-Path $MyResDir "icon.svg")
    magick $logo -define icon:auto-resize=256,64,48,32,16 (Join-Path $MyResDir "icon.ico")
    magick $logo -resize 32x32 (Join-Path $MyResDir "32x32.png")
    magick $logo -resize 64x64 (Join-Path $MyResDir "64x64.png")
    magick $logo -resize 128x128 (Join-Path $MyResDir "128x128.png")
    magick (Join-Path $MyResDir "128x128.png") -resize 200% (Join-Path $MyResDir "128x128@2x.png")
    Copy-Item (Join-Path $MyResDir "icon.ico") (Join-Path $MyResDir "tray-icon.ico")

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
  $dst = Join-Path "$RustDeskDir" 'flutter\windows\runner\resources\app_icon.ico'
  $dir = Split-Path $dst -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if (Test-Path $src) { Copy-Item $src $dst -Force; Ok ("Icon gesetzt: {0}" -f $dst) } else { Warn 'icon.ico nicht gefunden - Icon unveraendert.' }
}

# =================== build (client) ===================
function Build-Client {
  if (-not (Test-Path $Vcvars)) { Fail 'vcvarsall.bat nicht gefunden - CMD-Build wird nicht erzeugt.' }

  cmd /c $Vcvars amd64
  if ($LASTEXITCODE -ne 0) { Fail 'Build fehlgeschlagen.' }
  Ok ("vcvars environment loaded.")
  OK ("VCPKG_ROOT={0}, TRIPLET={1}" -f $VcpkgDir,$Env:VCPKG_DEFAULT_HOST_TRIPLET)
  
  Set-Location "$RustDeskDir"

  if ($SkipVcpkgInstall) {
    Say ("Skip vcpkg install (per parameter).")
  } else {
    Say("vcpkg installiere abhaengigkeiten")
    & $VcpkgDir\vcpkg install --triplet $Env:VCPKG_DEFAULT_HOST_TRIPLET --x-install-root="$VcpkgDir\installed"
    if ($LASTEXITCODE -ne 0) { Fail("vcpkg fehlgeschlagen.") }
    Ok("vcpkg installiert.")
  }
  
  if (!(Test-Path "target\debug")) {
      New-Item -Path target\debug -Type Directory | Out-Null
  }
  if (!(Test-Path "target\release")) {
      New-Item -Path target\release -Type Directory | Out-Null
  }
  Say ("Kopiere sciter.dll nach target..")
  Copy-Item "$SciterDllCache" target\debug -Force
  Copy-Item "$SciterDllCache" target\release -Force

  $Env:LIBCLANG_PATH = $LlvmBin
  Ok ("LIBCLANG_PATH={0}" -f $LlvmBin)

  if (-not $NoCleanBuild) {
    Say "[..] cargo clean"
    cargo clean
  } else {
    Say "[..] skip cargo clean (NoCleanBuild)"
  }
  
  $Env:RUSTFLAGS="-C target-feature=+crt-static"
  Say "Erstelle RustDesk Client Bibliothek"
  python .\build.py --hwcodec --flutter --vram --skip-portable-pack
  if ($LASTEXITCODE -ne 0) { Fail("build fehlgeschlagen.") }

  Say "Erstelle portable Exe"
  if (Test-Path "portable-pack") {
      Remove-Item -Path portable-pack -Recurse -Force -ErrorAction SilentlyContinue | Out-Null
  }
  New-Item -Path portable-pack -Type Directory | Out-Null
  Copy-Item (Join-Path flutter\build\windows\x64\runner\Release *) .\portable-pack -Force -Recurse

  Say "Entpacke... usbmmidd_v2"
  [System.IO.Compression.ZipFile]::ExtractToDirectory($UsbmmiddCache, $PWD)
  Remove-Item -Path usbmmidd_v2\Win32 -Recurse
  Remove-Item -Path usbmmidd_v2\deviceinstaller64.exe, usbmmidd_v2\deviceinstaller.exe, usbmmidd_v2\usbmmidd.bat
  Move-Item -Force usbmmidd_v2 portable-pack
  Ok ""

  if (Test-Path "$MyResDir\icon.png") {
    Say "Icon fuer Portable erstellen"
    Copy-Item (Join-Path $MyResDir "icon.svg") "portable-pack\data\flutter_assets\assets\icon.svg"
    Copy-Item (Join-Path $MyResDir "icon.png") "portable-pack\data\flutter_assets\assets\logo.png" -Force
    Ok ""
  }

  Say "Kopiere Runner.res nach Portable"
  $runner_res = Get-ChildItem . -Filter "Runner.res" -Recurse | Select-Object -First 1
  if (!$runner_res) {
    Warn "Runner.res: nicht gefunden"
  } else {
    Copy-Item $runner_res.FullName "libs\portable" -Force
    Ok ""
  }
  
  Say "Kopiere WindowInjection.dll"
  Copy-Item $WindowInjectionDLL .\portable-pack -Force
  Ok ""

  $exe = "rustdesk.exe"
  if ($AppName -ne "rustdesk") {
    $stem = ($AppName -replace '[^A-Za-z0-9._-]','-') -replace '-{2,}','-'
    $stem = $stem.Trim('-'); if ([string]::IsNullOrWhiteSpace($stem)) { $stem = 'rustdesk' }
    $exe  = "$stem.exe"
    #darf nicht umbenannt werden
    #Move-Item "portable-pack\rustdesk.exe" (Join-Path portable-pack $exe)
  }
  Say "Deaktiviere DPI awareness fuer bessere Aufloesung"
  ReplaceInFile -File res\manifest.xml -UseRegex -pattern ".*dpiAware.*" -NewString ""
  Ok ""

  Push-Location .\libs\portable
  pip3 install -r requirements.txt
  python .\generate.py -f ..\..\portable-pack\ -o . -e "..\..\portable-pack\rustdesk.exe"
  Pop-Location
  Move-Item .\target\release\rustdesk-portable-packer.exe (Join-Path $Root $exe) -Force
  
  Ok ("Portable build Fertig! {0}" -f (Join-Path $Root $exe))
}

function Ask-And-Uninstall {
  $tools = @(
    "Git.Git",
    "Rustlang.Rustup",
    "Rustlang.Rust.MSVC",
    "Rustlang.Rust.GNU",
    "Python.Python.3.13",
    "LLVM.LLVM",
    "ImageMagick.Q16-HDRI",
    "Microsoft.VisualStudio.2022.BuildTools"
  )
  $removeTools = @()
  foreach ($tool in $tools) {
    winget list -e --id $tool | Out-Null
    if ($LASTEXITCODE -eq 0) { 
      if ($tool -eq "Microsoft.VisualStudio.2022.BuildTools") {
        say "Visual Studio 2022 Build Tools, C++ Toolchain, Win10 SDK, CMake"
      }
      Say $tool
      $removeTools += $tool
    }
  }

  say "(x) - Einzeln abfragen"
  $answer = Read-Host 'Sollen alle oben genannten Tools deinstalliert werden? (j/n/x)'
  if ($answer -in @('J','j','Y','y','Ja','ja','Yes','yes','X','x')) {
    $ask = $answer -in @('X','x')
    foreach ($tool in $removeTools) {
      if ($tool -eq "Microsoft.VisualStudio.2022.BuildTools") {continue}
      if ($ask) {
        $answer = Read-Host "Soll [$tool] deinstalliert werden? (j/n)"
        if ($answer -notin @('J','j','Y','y','Ja','ja','Yes','yes')) {
          continue
        }
      }
      Say "Entferne $tool mit winget..."
      winget uninstall --id $tool --exact --silent --accept-source-agreements --purge --force | Out-Null
      Ok "Fertig"
    }
    if ($ask -and "Microsoft.VisualStudio.2022.BuildTools" -in $removeTools) {
      $answer = Read-Host "Soll [Visual Studio 2022 Build Tools, C++ Toolchain, Win10 SDK, CMake] deinstalliert werden? (j/n)"
      if ($answer -in @('J','j','Y','y','Ja','ja','Yes','yes')) {
        Say "Entferne VS Build Tools..."
        winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --silent --accept-package-agreements --accept-source-agreements --force --override "--remove Microsoft.VisualStudio.Workload.VCTools --remove Microsoft.VisualStudio.Component.Windows10SDK.19041 --remove Microsoft.VisualStudio.Component.VC.CMake.Project --includeRecommended --passive --norestart" | Out-Null
        while ((Get-Process -Name setup -ErrorAction SilentlyContinue).MainWindowTitle -eq "Visual Studio Installer") {
          Start-Sleep 2
        }
        winget uninstall --id Microsoft.VisualStudio.2022.BuildTools --exact --silent --accept-source-agreements --purge --force | Out-Null
        Ok "Fertig"
      }
    }
  }
}

# =================== main ===================
try {
  if ($Uninstall) {
    Ask-And-Uninstall
    return 
  }

  Get-Inputs
  Ensure-RootOnNtfs -Root $Root
  Test-And-Handle-ASLR
  
  Ensure-LLVM
  Ensure-Python
  Ensure-RustToolchain
  Find-Vcvars
  Find-MSBuildTools
  Ensure-Vcpkg
  Ensure-Flutter
  Ensure-usbmmidd
  Ensure-ImageMagick
  Ensure-SciterDll

  Clone-RustDesk
  Generate-RustBridge
  Generate-RustDeskTempTopMostWindow
  Patch-Config
  Patch-Client
  Generate-ResourcesFromLogo
  Patch-IconBase64
  Copy-Resources
  Set-Icon
  
  Build-Client

} catch {
  Fail $_
} finally {
  Set-Location $Root
  $ProgressPreference = $defaultProgressPreference
}
