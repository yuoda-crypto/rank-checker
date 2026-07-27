@echo off
chcp 65001 > nul
echo.
echo ============================================
echo   順位チェッカー セットアップ
echo ============================================
echo.
echo インストールしています。少し待ってね...

set "DEST=%LOCALAPPDATA%\RankChecker"

xcopy "%~dp0app" "%DEST%" /E /I /Y /Q > nul
if errorlevel 1 (
    echo.
    echo [エラー] ファイルのコピーに失敗しました。
    echo このフォルダをデスクトップなどに移動してから、もう一度実行してみてください。
    pause
    exit /b 1
)

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\順位チェッカー.lnk'); $s.TargetPath='%DEST%\RankChecker.exe'; $s.WorkingDirectory='%DEST%'; $s.IconLocation='%DEST%\RankChecker.exe'; $s.Save()"
if errorlevel 1 (
    echo.
    echo [エラー] ショートカットの作成に失敗しました。
    echo %DEST% の RankChecker.exe を直接ダブルクリックしても使えます。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   セットアップ完了！
echo   デスクトップに「順位チェッカー」ができました。
echo   ダブルクリックで起動してね。
echo ============================================
echo.
pause
