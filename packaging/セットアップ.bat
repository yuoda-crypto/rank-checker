@echo off
echo.
echo ============================================
echo   順位チェッカー セットアップ
echo ============================================
echo.
echo インストールしています。少し待ってね...

set "DEST=%LOCALAPPDATA%\RankChecker"

if not exist "%~dp0app\RankChecker.exe" (
    echo.
    echo [エラー] ZIPの中から直接実行されています。
    echo 先にZIPを右クリック→「すべて展開」してから、
    echo 展開されたフォルダの中の「セットアップ.bat」を実行してください。
    pause
    exit /b 1
)

xcopy "%~dp0app" "%DEST%" /E /I /Y /Q > nul
if errorlevel 1 (
    echo.
    echo [エラー] ファイルのコピーに失敗しました。
    echo このフォルダをデスクトップなどに移動してから、もう一度実行してみてください。
    pause
    exit /b 1
)

rem ダウンロード由来のブロック属性(Zone.Identifier)が残っていると
rem .NETがDLLの読み込みを拒否してアプリが起動しないため、ここで全ファイル解除する
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%DEST%' -Recurse -File | Unblock-File -ErrorAction SilentlyContinue"

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
