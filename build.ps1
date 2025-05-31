# 首先必须激活虚拟环境，在虚拟环境下运行
.venv\Scripts\Activate.ps1
$scripts = @("setDNS.py", "setHosts.py", "setHosts_Classic.py")
if (Test-Path -Path "dist") {
    Remove-Item -Recurse -Force "dist"
}
foreach ($script in $scripts) {
    $icoName = $script -replace '\.py$', '.ico'

    if (Test-Path -Path $icoName) {
        pyinstaller --clean --onefile $script --uac-admin --icon $icoName --hidden-import dns
    }
    else {
        pyinstaller --clean --onefile $script --uac-admin --hidden-import dns
    }
    # pyinstaller --onefile $script --uac-admin 
    
    $exeName = $script -replace '\.py$', ''
    Move-Item "dist\$exeName.exe" "dist\$exeName-Windows-x64.exe"
}