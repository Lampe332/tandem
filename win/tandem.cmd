@echo off
rem cmd and PowerShell entry point on Windows: they cannot run the extension-less bin\tandem script directly.
python "%~dp0..\bin\tandem" %*
