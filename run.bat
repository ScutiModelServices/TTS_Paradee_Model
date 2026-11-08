@echo off
REM Starts the ttsparadee service on port 29321 (syncs .venv with uv on each run). The first start downloads 8.5 MB plus the phonemizer data.
REM LABEL: TTSParadee Service
REM DESCRIPTION: Shared HTTP service that runs Paradee text to speech on the CPU for every project.
cd /d "%~dp0"
if /i "%~1"=="run" goto run
if /i "%~1"=="deploy" goto deploy
if /i "%~1"=="zip" goto zip

:menu
echo.
echo TTS_Paradee_Model
echo.
echo   1  Run the service
echo   2  Deploy ^(publish to live, push .env, release^)
echo   3  Build the release ZIP
echo   Q  Quit
echo.
choice /c 123Q /n /m "What do you want to do? "
if errorlevel 4 exit /b 0
if errorlevel 3 goto zip
if errorlevel 2 goto deploy
goto run

:run
uv sync
if errorlevel 1 (
    echo uv sync failed. Install uv: winget install astral-sh.uv
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

python src/app.py --dotenv
pause
exit /b 0

rem ---- Deploy: publish the code to the live branch and/or copy the git-ignored .env to the
rem deployment machine in ONE ssh session (one password prompt).
rem The server refuses root over ssh, so we log in as a normal user. If the copy cannot write,
rem option 6 copies the one-time "chown" command and opens the server's web terminal (root shell).
rem The service runs from its own docker-compose.yml in the deploy folder, with .env beside it.
rem Override the target: set DEPLOY_HOST / DEPLOY_DIR / DEPLOY_WEB_PORT before running, or put KEY=VALUE lines in the
rem git-ignored tools\deploy.local.ini (copy tools\deploy.local.example.ini) so the address stays out of the repository.
:deploy
setlocal enabledelayedexpansion
if exist "%~dp0tools\deploy.local.ini" for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0tools\deploy.local.ini") do set "%%A=%%B"
if not defined DEPLOY_HOST set "DEPLOY_HOST=lex@scutiserver1.local"
if not defined DEPLOY_DIR set "DEPLOY_DIR=/media/ZimaOS-HD/Apps/ModelServices/TTS_Paradee_Model"
if not defined DEPLOY_WEB_PORT set "DEPLOY_WEB_PORT=7681"
for %%I in ("%~dp0.") do set "ROOT=%%~fI"

:d_menu
echo.
echo Target: %DEPLOY_HOST%:%DEPLOY_DIR%
echo.
echo   1  Publish code to the live branch
echo   2  Push .env to the server
echo   3  Both (publish, then push)
echo   4  Preview publish ^(dry run^)
echo   5  Preview push ^(dry run^)
echo   6  Fix server folder permissions ^(chown helper^)
echo   R  Release ^(publish to live, then tag VERSION^)
echo   Q  Quit
echo.
choice /c 123456RQ /n /m "What do you want to do? "
set "PICK=%errorlevel%"
if %PICK%==8 exit /b 0
if %PICK%==1 call :d_publish
if %PICK%==2 call :d_push
if %PICK%==3 call :d_publish && call :d_push
if %PICK%==4 call :d_publish --dry-run
if %PICK%==5 call :d_push --dry-run
if %PICK%==6 call :d_fixperms
if %PICK%==7 call :d_publish --release
goto d_menu

rem :d_publish [--dry-run | --release]
:d_publish
py "%ROOT%\tools\publish_live.py" %*
if errorlevel 1 (echo Publish failed. & exit /b 1)
if /i not "%~1"=="--dry-run" call :d_reminder "Code is on the live branch. On the server:" "git pull"
exit /b 0

rem :d_push [--dry-run]
rem Packs every existing file into one tar stream and unpacks it over a single ssh connection,
rem so the password is asked once.
:d_push
set "DRY="
if /i "%~1"=="--dry-run" set "DRY=1"
set "LIST=%TEMP%\ttsparadee_push.txt"
type nul > "%LIST%"
set /a COUNT=0
call :d_add ".env"
echo.
if !COUNT!==0 (echo Nothing to send. & del "%LIST%" & exit /b 0)
if defined DRY (echo Would send !COUNT! file^(s^). & del "%LIST%" & exit /b 0)
echo Sending !COUNT! file^(s^) in one ssh session. Enter the password once.
tar -cf - -C "%ROOT%" -T "%LIST%" | ssh "%DEPLOY_HOST%" "tar -xf - --no-same-permissions -C '%DEPLOY_DIR%'"
set "RC=!errorlevel!"
del "%LIST%"
if not "!RC!"=="0" (
    echo.
    echo FAILED. Nothing may have been copied. Check the error above.
    echo If it says Permission denied, press Y to run the chown helper, then run this option again.
    choice /c YN /n /m "Run the chown helper now? "
    if !errorlevel!==1 call :d_fixperms
    exit /b 1
)
echo Done.
call :d_reminder ".env is on the server. In the web terminal:" "docker compose up -d --build" "(run git pull here first if the code changed)"
exit /b 0

rem :d_add <path relative to the TTS_Paradee_Model root, same path on the server>
:d_add
if not exist "%ROOT%\%~1" (echo skip    %~1 ^(missing locally^) & exit /b 0)
>>"%LIST%" echo %~1
set /a COUNT+=1
echo %~1
exit /b 0

rem The server's ssh user cannot chown by itself; root only exists in the web terminal.
rem Copies the command to the clipboard and opens that terminal: paste, Enter.
:d_fixperms
for /f "tokens=1,2 delims=@" %%U in ("%DEPLOY_HOST%") do (set "SVUSER=%%U" & set "SVHOST=%%V")
set "FIXCMD=chown -R %SVUSER% %DEPLOY_DIR%"
echo %FIXCMD%| clip
echo.
echo Copied to the clipboard:
echo   %FIXCMD%
echo Opening http://%SVHOST%:%DEPLOY_WEB_PORT%/ ^(root shell^). Paste the command there and press Enter.
start "" "http://%SVHOST%:%DEPLOY_WEB_PORT%/"
exit /b 0

rem :d_reminder <heading> <command> [note]
:d_reminder
echo.
echo ---- NEXT ON THE SERVER ----
echo %~1
echo   cd %DEPLOY_DIR%
set "SHOWCMD=%~2"
echo   !SHOWCMD!
if not "%~3"=="" echo   %~3
echo ----------------------------
exit /b 0

rem ---- ZIP: build _zip_versions\TTS_Paradee_Model-<VERSION>.zip, select it in Explorer, and open GitHub's new-release page for that tag
rem so the zip can be dragged onto it. Run it after the release is tagged (Deploy, option R).
:zip
setlocal
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
py -3 "%ROOT%\tools\make_zip.py" || python "%ROOT%\tools\make_zip.py" || (echo ZIP creation failed. & pause & exit /b 1)
set /p VER=<"%ROOT%\VERSION"
start "" explorer /select,"%ROOT%\_zip_versions\TTS_Paradee_Model-%VER%.zip"
start "" "https://github.com/ScutiModelServices/TTS_Paradee_Model/releases/new?tag=v%VER%"
echo.
echo Drag _zip_versions\TTS_Paradee_Model-%VER%.zip onto the release page, paste the notes, and publish.
pause
exit /b 0
