; Subtitld's Windows installer (NSIS, Modern UI 2).
;
;   makensis /DVERSION=26.10.09.1200 nsis-installer.nsi
;
; Installs just for the current user (no administrator rights, under
; %LOCALAPPDATA%\Programs) or for everyone (Program Files: Setup starts
; itself again as administrator for that). An earlier install decides which
; is offered first. Command line: /S installs silently, for the current user
; unless /AllUsers is given; /LANG=1046 picks a language (testing).
;
; The pictures and icons are rendered by packaging/branding/render-icons.py.
;
; File types, for the user or for everyone as Subtitld is installed: .usfx,
; Subtitld's own, opens in Subtitld, with its own icon and, in Explorer, the
; picture it carries (UsfxThumbnail.dll); .srt, .vtt, .ass and .ssa get
; Subtitld in "Open with" and in Settings > Default apps, without taking
; them over. The same types as the MSIX package's.

Unicode True
ManifestDPIAware true

!ifndef VERSION
  !define VERSION "0.0.0.0"
!endif
!define APPNAME "Subtitld"
!define PUBLISHER "Subtitld"
!define WEBSITE "https://subtitld.org"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"
!define CLASSES "Software\Classes"
!define CAPABILITIES "Software\${APPNAME}\Capabilities"
!define PROGID_PROJECT "Subtitld.usfx"
!define PROGID_SUBTITLE "Subtitld.subtitle"
; The thumbnail provider (packaging/windows/thumbnail), and Windows' key for
; a type's thumbnail handler.
!define THUMBNAIL_CLSID "{59A8C040-1202-4033-8DB5-F5E7A7709527}"
!define THUMBNAIL_HANDLER "{e357fccd-a995-4576-b01f-234630154e96}"

Name "${APPNAME}"
OutFile "Subtitld-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
RequestExecutionLevel user
BrandingText "${APPNAME} ${VERSION}"

!include MUI2.nsh
!include LogicLib.nsh
!include FileFunc.nsh
!include nsDialogs.nsh

!define MUI_ICON "packaging\nsis\installer.ico"
!define MUI_UNICON "packaging\nsis\uninstaller.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "packaging\nsis\wizard.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "packaging\nsis\wizard.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "packaging\nsis\header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "packaging\nsis\header.bmp"
!define MUI_ABORTWARNING
; The users page below is our own, but its texts are the language files'.
!define MULTIUSER_INSTALLMODEPAGE

Var IsAdmin        ; 1 when running as administrator
Var InstallMode    ; CurrentUser or AllUsers
Var Relaunched     ; 1 when started again as administrator, to install for everyone
Var RadioMe
Var RadioAll

; Installer pages
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfRelaunched
!insertmacro MUI_PAGE_WELCOME
Page custom UsersPage UsersPageLeave
!define MUI_PAGE_CUSTOMFUNCTION_SHOW DirectoryShown
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(DESKTOP_SHORTCUT)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateDesktopShortcut
!insertmacro MUI_PAGE_FINISH

; Uninstaller pages
!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

; The app's languages; Windows' own language picks one.
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "PortugueseBR"

LangString DESKTOP_SHORTCUT ${LANG_ENGLISH} "Create a desktop shortcut"
LangString DESKTOP_SHORTCUT ${LANG_PORTUGUESEBR} "Criar um atalho na área de trabalho"
LangString TYPE_PROJECT ${LANG_ENGLISH} "Subtitld project"
LangString TYPE_PROJECT ${LANG_PORTUGUESEBR} "Projeto do Subtitld"
LangString TYPE_SUBTITLE ${LANG_ENGLISH} "Subtitles"
LangString TYPE_SUBTITLE ${LANG_PORTUGUESEBR} "Legendas"
LangString APP_DESCRIPTION ${LANG_ENGLISH} "Create, edit, transcribe, translate and dub subtitles"
LangString APP_DESCRIPTION ${LANG_PORTUGUESEBR} "Crie, edite, transcreva, traduza e duble legendas"

VIProductVersion "${VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "CompanyName" "${PUBLISHER}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${APPNAME} Setup"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "GNU GPL v3"


; ── Install modes ──────────────────────────────────────────────────────────

Function CheckAdmin
  UserInfo::GetAccountType
  Pop $0
  ${If} $0 == "Admin"
    StrCpy $IsAdmin 1
  ${Else}
    StrCpy $IsAdmin 0
  ${EndIf}
FunctionEnd

Function UseCurrentUser
  StrCpy $InstallMode "CurrentUser"
  SetShellVarContext current
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${Else}
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${APPNAME}"
  ${EndIf}
FunctionEnd

Function UseAllUsers
  StrCpy $InstallMode "AllUsers"
  SetShellVarContext all
  ReadRegStr $0 HKLM "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $0 == ""
    ; Earlier installers registered in the 32-bit view.
    SetRegView 32
    ReadRegStr $0 HKLM "${UNINSTALL_KEY}" "InstallLocation"
    SetRegView 64
  ${EndIf}
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${Else}
    StrCpy $INSTDIR "$PROGRAMFILES64\${APPNAME}"
  ${EndIf}
FunctionEnd

; Start Setup again as administrator, to install for everyone. Returns (on
; the stack) 1 if it started, 0 if not (the prompt was declined).
Function Relaunch
  Exch $1                     ; extra parameters
  System::Call 'shell32::ShellExecuteW(p $HWNDPARENT, w "runas", w "$EXEPATH", w "/AllUsers $1", p 0, i 1) p.r0'
  ${If} $0 > 32
    StrCpy $1 1
  ${Else}
    StrCpy $1 0
  ${EndIf}
  Exch $1
FunctionEnd

Function .onInit
  SetRegView 64
  Call CheckAdmin
  StrCpy $Relaunched 0
  ${GetParameters} $R0

  ClearErrors
  ${GetOptions} $R0 "/LANG=" $R1
  ${IfNot} ${Errors}
    StrCpy $LANGUAGE $R1
  ${EndIf}

  ; For everyone if asked, or if that is how it was installed before.
  ClearErrors
  ${GetOptions} $R0 "/AllUsers" $R1
  ${IfNot} ${Errors}
    StrCpy $Relaunched 1
    Call UseAllUsers
  ${Else}
    ReadRegStr $R1 HKCU "${UNINSTALL_KEY}" "InstallLocation"
    ReadRegStr $R2 HKLM "${UNINSTALL_KEY}" "InstallLocation"
    SetRegView 32
    ReadRegStr $R3 HKLM "${UNINSTALL_KEY}" "InstallLocation"
    SetRegView 64
    ${If} $R1 == ""
    ${AndIf} "$R2$R3" != ""
      Call UseAllUsers
    ${Else}
      Call UseCurrentUser
    ${EndIf}
  ${EndIf}

  ${If} $InstallMode == "AllUsers"
  ${AndIf} $IsAdmin == 0
  ${AndIf} ${Silent}
    Push "/S"
    Call Relaunch
    Pop $0
    ${If} $0 == 1
      Quit
    ${EndIf}
    Abort
  ${EndIf}
FunctionEnd

; Started again as administrator, Setup goes straight to the folder page; from
; there, Back leads to the pages it skipped, not out of Setup.
Function SkipIfRelaunched
  ${If} $Relaunched == 1
    Abort
  ${EndIf}
FunctionEnd

Function DirectoryShown
  StrCpy $Relaunched 0
FunctionEnd

Function UsersPage
  ${If} $Relaunched == 1
    Abort
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "$(MULTIUSER_TEXT_INSTALLMODE_TITLE)" "$(MULTIUSER_TEXT_INSTALLMODE_SUBTITLE)"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 30u "$(MULTIUSER_INNERTEXT_INSTALLMODE_TOP)"
  Pop $0
  ${NSD_CreateRadioButton} 10u 40u -10u 12u "$(MULTIUSER_INNERTEXT_INSTALLMODE_CURRENTUSER)"
  Pop $RadioMe
  ${NSD_CreateRadioButton} 10u 58u -10u 12u "$(MULTIUSER_INNERTEXT_INSTALLMODE_ALLUSERS)"
  Pop $RadioAll
  ${If} $InstallMode == "AllUsers"
    ${NSD_Check} $RadioAll
  ${Else}
    ${NSD_Check} $RadioMe
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function UsersPageLeave
  ${NSD_GetState} $RadioAll $0
  ${If} $0 == ${BST_CHECKED}
    ${If} $IsAdmin == 0
      Push ""
      Call Relaunch
      Pop $0
      ${If} $0 == 1
        Quit
      ${EndIf}
      Abort                 ; declined: stay on this page
    ${EndIf}
    Call UseAllUsers
  ${Else}
    Call UseCurrentUser
  ${EndIf}
FunctionEnd


; ── File types ─────────────────────────────────────────────────────────────

; A type Subtitld opens: Subtitld under "Open with" for it, among the types
; Settings > Default apps offers for Subtitld, and in Subtitld.exe's own list.
!macro OpenWith EXT PROGID
  WriteRegStr SHCTX "${CLASSES}\${EXT}\OpenWithProgids" "${PROGID}" ""
  WriteRegStr SHCTX "${CAPABILITIES}\FileAssociations" "${EXT}" "${PROGID}"
  WriteRegStr SHCTX "${CLASSES}\Applications\Subtitld.exe\SupportedTypes" "${EXT}" ""
!macroend

!macro ForgetOpenWith EXT PROGID
  DeleteRegValue SHCTX "${CLASSES}\${EXT}\OpenWithProgids" "${PROGID}"
  DeleteRegKey /ifempty SHCTX "${CLASSES}\${EXT}\OpenWithProgids"
  DeleteRegKey /ifempty SHCTX "${CLASSES}\${EXT}"
!macroend

!macro ProgId PROGID NAME ICON
  WriteRegStr SHCTX "${CLASSES}\${PROGID}" "" "${NAME}"
  WriteRegStr SHCTX "${CLASSES}\${PROGID}\DefaultIcon" "" '${ICON}'
  WriteRegStr SHCTX "${CLASSES}\${PROGID}\shell\open\command" "" '"$INSTDIR\Subtitld.exe" "%1"'
!macroend

Function RegisterFileTypes
  ; Projects have their own icon; subtitles, Subtitld's.
  !insertmacro ProgId "${PROGID_PROJECT}" "$(TYPE_PROJECT)" '"$INSTDIR\usfx.ico",0'
  !insertmacro ProgId "${PROGID_SUBTITLE}" "$(TYPE_SUBTITLE)" '"$INSTDIR\Subtitld.exe",0'

  WriteRegStr SHCTX "${CLASSES}\Applications\Subtitld.exe" "FriendlyAppName" "${APPNAME}"
  WriteRegStr SHCTX "${CLASSES}\Applications\Subtitld.exe\shell\open\command" "" '"$INSTDIR\Subtitld.exe" "%1"'
  WriteRegStr SHCTX "${CAPABILITIES}" "ApplicationName" "${APPNAME}"
  WriteRegStr SHCTX "${CAPABILITIES}" "ApplicationDescription" "$(APP_DESCRIPTION)"
  WriteRegStr SHCTX "Software\RegisteredApplications" "${APPNAME}" "${CAPABILITIES}"

  ; .usfx is Subtitld's own: it opens in Subtitld.
  !insertmacro OpenWith ".usfx" "${PROGID_PROJECT}"
  WriteRegStr SHCTX "${CLASSES}\.usfx" "" "${PROGID_PROJECT}"
  ; and Explorer shows the picture it carries.
  ${If} ${FileExists} "$INSTDIR\UsfxThumbnail.dll"
    WriteRegStr SHCTX "${CLASSES}\CLSID\${THUMBNAIL_CLSID}" "" "Subtitld project thumbnail"
    WriteRegStr SHCTX "${CLASSES}\CLSID\${THUMBNAIL_CLSID}\InprocServer32" "" "$INSTDIR\UsfxThumbnail.dll"
    WriteRegStr SHCTX "${CLASSES}\CLSID\${THUMBNAIL_CLSID}\InprocServer32" "ThreadingModel" "Apartment"
    WriteRegStr SHCTX "${CLASSES}\.usfx\ShellEx\${THUMBNAIL_HANDLER}" "" "${THUMBNAIL_CLSID}"
  ${EndIf}
  ; The subtitle formats keep whatever opens them now.
  !insertmacro OpenWith ".srt" "${PROGID_SUBTITLE}"
  !insertmacro OpenWith ".vtt" "${PROGID_SUBTITLE}"
  !insertmacro OpenWith ".ass" "${PROGID_SUBTITLE}"
  !insertmacro OpenWith ".ssa" "${PROGID_SUBTITLE}"

  ; Tell Explorer, so the icons and "Open with" change now, not at the next sign-in.
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
FunctionEnd

Function un.UnregisterFileTypes
  ; The thumbnail handler, if it is still ours.
  ReadRegStr $0 SHCTX "${CLASSES}\.usfx\ShellEx\${THUMBNAIL_HANDLER}" ""
  ${If} $0 == "${THUMBNAIL_CLSID}"
    DeleteRegKey SHCTX "${CLASSES}\.usfx\ShellEx\${THUMBNAIL_HANDLER}"
    DeleteRegKey /ifempty SHCTX "${CLASSES}\.usfx\ShellEx"
  ${EndIf}
  DeleteRegKey SHCTX "${CLASSES}\CLSID\${THUMBNAIL_CLSID}"
  ; .usfx's default, unless something else has taken it since.
  ReadRegStr $0 SHCTX "${CLASSES}\.usfx" ""
  ${If} $0 == "${PROGID_PROJECT}"
    DeleteRegValue SHCTX "${CLASSES}\.usfx" ""
  ${EndIf}
  !insertmacro ForgetOpenWith ".usfx" "${PROGID_PROJECT}"
  !insertmacro ForgetOpenWith ".srt" "${PROGID_SUBTITLE}"
  !insertmacro ForgetOpenWith ".vtt" "${PROGID_SUBTITLE}"
  !insertmacro ForgetOpenWith ".ass" "${PROGID_SUBTITLE}"
  !insertmacro ForgetOpenWith ".ssa" "${PROGID_SUBTITLE}"

  DeleteRegKey SHCTX "${CLASSES}\${PROGID_PROJECT}"
  DeleteRegKey SHCTX "${CLASSES}\${PROGID_SUBTITLE}"
  DeleteRegKey SHCTX "${CLASSES}\Applications\Subtitld.exe"
  DeleteRegValue SHCTX "Software\RegisteredApplications" "${APPNAME}"
  DeleteRegKey SHCTX "${CAPABILITIES}"
  DeleteRegKey /ifempty SHCTX "Software\${APPNAME}"

  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
FunctionEnd


; ── Install ────────────────────────────────────────────────────────────────

Section "Install"
  SetOutPath $INSTDIR
  ; Explorer may hold the thumbnail extension open: a loaded DLL cannot be
  ; replaced, but it can be moved out of the way.
  ${If} ${FileExists} "$INSTDIR\UsfxThumbnail.dll"
    Delete "$INSTDIR\UsfxThumbnail.old"
    Rename "$INSTDIR\UsfxThumbnail.dll" "$INSTDIR\UsfxThumbnail.old"
    Delete /REBOOTOK "$INSTDIR\UsfxThumbnail.old"
  ${EndIf}
  File /r "dist\Subtitld\*.*"
  File "packaging\nsis\usfx.ico"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\Subtitld.exe"
  Call RegisterFileTypes

  WriteRegStr SHCTX "${UNINSTALL_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Subtitld.exe"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "URLInfoAbout" "${WEBSITE}"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "HelpLink" "${WEBSITE}"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr SHCTX "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegDWORD SHCTX "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD SHCTX "${UNINSTALL_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD SHCTX "${UNINSTALL_KEY}" "EstimatedSize" $0

  ; What earlier installers left: an entry in the 32-bit view, and the Start
  ; menu shortcut in a folder of its own, in the installing user's menu.
  ${If} $InstallMode == "AllUsers"
    SetRegView 32
    DeleteRegKey HKLM "${UNINSTALL_KEY}"
    SetRegView 64
  ${EndIf}
  SetShellVarContext current
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  ${If} $InstallMode == "AllUsers"
    SetShellVarContext all
  ${EndIf}
SectionEnd

Function CreateDesktopShortcut
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\Subtitld.exe"
FunctionEnd

Function LaunchApp
  ${If} $IsAdmin == 1
    ; Not as administrator: Explorer starts it as the user.
    Exec '"$WINDIR\explorer.exe" "$INSTDIR\Subtitld.exe"'
  ${Else}
    Exec '"$INSTDIR\Subtitld.exe"'
  ${EndIf}
FunctionEnd


; ── Uninstall ──────────────────────────────────────────────────────────────

Function un.onInit
  SetRegView 64
  UserInfo::GetAccountType
  Pop $0
  ; Installed for everyone if everyone's entry points here: that needs
  ; administrator rights to undo.
  ReadRegStr $1 HKLM "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $1 == $INSTDIR
    SetShellVarContext all
    ${If} $0 != "Admin"
      ; Start uninstall.exe itself again (not this temporary copy of it), so
      ; that is the name Windows' prompt shows.
      ${If} ${Silent}
        StrCpy $2 "/S"
      ${Else}
        StrCpy $2 ""
      ${EndIf}
      System::Call 'shell32::ShellExecuteW(p 0, w "runas", w "$INSTDIR\uninstall.exe", w "$2", p 0, i 1) p.r0'
      ${If} $0 > 32
        Quit
      ${EndIf}
      Abort
    ${EndIf}
  ${Else}
    SetShellVarContext current
  ${EndIf}
FunctionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  ; Only a folder that is Subtitld's: the directory page lets people pick any.
  ${If} ${FileExists} "$INSTDIR\Subtitld.exe"
    RMDir /r /REBOOTOK "$INSTDIR"         ; the thumbnail DLL, if Explorer holds it
  ${Else}
    Delete "$INSTDIR\uninstall.exe"
    RMDir "$INSTDIR"
  ${EndIf}
  DeleteRegKey SHCTX "${UNINSTALL_KEY}"
  Call un.UnregisterFileTypes
SectionEnd
