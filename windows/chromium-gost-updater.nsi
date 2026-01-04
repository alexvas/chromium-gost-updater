; NSIS Installer Script для chromium-gost-updater
; Использование: makensis /DVERSION=1.0.0 /DOUTPUT=output.exe /DDISTDIR=dist chromium-gost-updater.nsi

!include "MUI2.nsh"
!include "FileFunc.nsh"

; Определяем переменные (могут быть переопределены через /D)
!ifndef VERSION
  !define VERSION "1.0.0"
!endif

!ifndef DISTDIR
  !define DISTDIR "dist"
!endif

; Настройки установщика
Name "Chromium Gost Updater"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\chromium-gost-updater"
InstallDirRegKey HKCU "Software\chromium-gost-updater" "InstallPath"
RequestExecutionLevel user

; Интерфейс
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!define MUI_HEADERIMAGE
!define MUI_WELCOMEPAGE_TITLE "Установка Chromium Gost Updater"
!define MUI_WELCOMEPAGE_TEXT "Этот мастер установит Chromium Gost Updater ${VERSION}$\r$\n$\r$\nПриложение для автоматической проверки и установки обновлений браузера Chromium Gost."

; Страницы установщика
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${DISTDIR}\NOTICE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\chromium-gost-updater.bat"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить Chromium Gost Updater"
!insertmacro MUI_PAGE_FINISH

; Страницы удаления
!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

; Языки
!insertmacro MUI_LANGUAGE "Russian"

; Секции установки
Section "Основные файлы" SecMain
    SectionIn RO  ; Обязательная секция
    
    SetOutPath "$INSTDIR"
    
    ; Копируем основные файлы
    File "${DISTDIR}\chromium-gost-updater.py"
    File "${DISTDIR}\chromium-gost-updater.toml"
    File "${DISTDIR}\chromium-gost-updater.bat"
    File /nonfatal "${DISTDIR}\install-task-scheduler.ps1"
    
    ; Копируем иконку если есть
    File /nonfatal "${DISTDIR}\chromium-gost-logo.png"
    
    ; Копируем документацию
    File /nonfatal "${DISTDIR}\README.md"
    File /nonfatal "${DISTDIR}\NOTICE.txt"
    
    ; Копируем Python и зависимости если есть (для portable версии)
    File /r /nonfatal "${DISTDIR}\python"
    
    ; Сохраняем путь установки в реестре
    WriteRegStr HKCU "Software\chromium-gost-updater" "InstallPath" "$INSTDIR"
    WriteRegStr HKCU "Software\chromium-gost-updater" "Version" "${VERSION}"
    
    ; Создаем ярлык в меню Пуск
    CreateDirectory "$SMPROGRAMS\Chromium Gost Updater"
    CreateShortCut "$SMPROGRAMS\Chromium Gost Updater\Chromium Gost Updater.lnk" "$INSTDIR\chromium-gost-updater.bat"
    CreateShortCut "$SMPROGRAMS\Chromium Gost Updater\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
    
    ; Устанавливаем задачу Task Scheduler
    IfFileExists "$INSTDIR\install-task-scheduler.ps1" 0 +3
    ExecWait 'powershell.exe -ExecutionPolicy Bypass -File "$INSTDIR\install-task-scheduler.ps1" -InstallDir "$INSTDIR"'
    
    ; Создаем запись для удаления
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    
    ; Регистрируем в "Установка и удаление программ"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "DisplayName" "Chromium Gost Updater"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "Publisher" "Chromium Gost Updater"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater" "NoRepair" 1
SectionEnd

; Описания секций
LangString DESC_SecMain ${LANG_RUSSIAN} "Основные файлы приложения (обязательно)"

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} $(DESC_SecMain)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; Секция удаления
Section "Uninstall"
    ; Удаляем задачу Task Scheduler
    ExecWait 'powershell.exe -Command "Unregister-ScheduledTask -TaskName ChromiumGostUpdater -Confirm:`$false -ErrorAction SilentlyContinue"'
    
    ; Удаляем файлы
    Delete "$INSTDIR\chromium-gost-updater.py"
    Delete "$INSTDIR\chromium-gost-updater.toml"
    Delete "$INSTDIR\chromium-gost-updater.bat"
    Delete "$INSTDIR\install-task-scheduler.ps1"
    Delete "$INSTDIR\chromium-gost-logo.png"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\NOTICE.txt"
    Delete "$INSTDIR\Uninstall.exe"
    
    ; Удаляем директорию Python если есть
    RMDir /r "$INSTDIR\python"
    
    ; Удаляем директорию установки если пуста
    RMDir "$INSTDIR"
    
    ; Удаляем ярлыки
    RMDir /r "$SMPROGRAMS\Chromium Gost Updater"
    
    ; Удаляем записи реестра
    DeleteRegKey HKCU "Software\chromium-gost-updater"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\chromium-gost-updater"
SectionEnd
