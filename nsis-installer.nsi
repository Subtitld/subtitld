!define APPNAME "Subtitld"
!define COMPANYNAME "Subtitld"
!define DESCRIPTION "Subtitle editor and transcription tool"
!define HELPURL "https://subtitld.org"

Name "${APPNAME}"
OutFile "Subtitld-Setup.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
RequestExecutionLevel admin

Page directory
Page instfiles

Section "Install"
    SetOutPath $INSTDIR
    File /r "dist\Subtitld\*.*"
    
    WriteUninstaller "$INSTDIR\uninstall.exe"
    
    CreateDirectory "$SMPROGRAMS\${APPNAME}"
    CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\Subtitld.exe"
    CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\Subtitld.exe"
    
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "DisplayName" "${APPNAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "UninstallString" "$INSTDIR\uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "Publisher" "${COMPANYNAME}"
SectionEnd

Section "Uninstall"
    Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
    Delete "$DESKTOP\${APPNAME}.lnk"
    RMDir "$SMPROGRAMS\${APPNAME}"
    
    RMDir /r "$INSTDIR"
    
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"
SectionEnd
