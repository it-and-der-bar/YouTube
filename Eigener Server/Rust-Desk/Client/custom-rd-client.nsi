!define APPNAME "My Remote Client"
!define TMPDIR  "$TEMP\\${APPNAME}_$PID"
!define SRCDIR  "rustdesk\pkg"
!define LOGFILE "$TEMP\\${APPNAME}_run.log"

RequestExecutionLevel user
OutFile "My-Remote-Client.exe"


!define MUI_ICON "my-ressources/_icon.ico"
!define MUI_UNICON "my-ressources/icon.ico"
Icon "my-ressources/icon.ico"
UninstallIcon "my-ressources/icon.ico"

VIAddVersionKey "FileDescription" "RustDesk My-Edition"
VIAddVersionKey "ProductName"     "RustDesk"
VIAddVersionKey "CompanyName"     ""
VIAddVersionKey "LegalCopyright"  "free to use"
VIProductVersion 1.4.2.0


Unicode true
SilentInstall silent
ShowInstDetails nevershow
SetCompressor /SOLID lzma

Var ExitCode

Section
  ; Log starten
  FileOpen $9 "${LOGFILE}" w

  ; Temp-Ordner
  CreateDirectory "${TMPDIR}"
  SetOutPath "${TMPDIR}"
  FileWrite $9 "Extract to: ${TMPDIR}$\r$\n"

  ; Payload entpacken
  File /r "${SRCDIR}\*.*"
  FileWrite $9 "Extracted payload.$\r$\n"

  ; Ausführen (Working-Dir = TMP)
  FileWrite $9 "Launching: ${TMPDIR}\rustdesk.exe$\r$\n"
  ExecWait '"${TMPDIR}\rustdesk.exe"' $ExitCode
  FileWrite $9 "rustdesk exited with code: $ExitCode$\r$\n"

  ; Aufräumen (nur wenn nicht im Debug)
  RMDir /r "${TMPDIR}"
  FileWrite $9 "Cleanup done.$\r$\n"
  FileClose $9
SectionEnd
