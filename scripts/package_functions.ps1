# azd prepackage hook (Windows): same as package_functions.sh
$root = Split-Path -Parent $PSScriptRoot
Remove-Item -Recurse -Force "$root/functions/aiip" -ErrorAction SilentlyContinue
Copy-Item -Recurse "$root/src/aiip" "$root/functions/aiip"
Get-ChildItem "$root/functions/aiip" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Write-Output "copied src/aiip -> functions/aiip"
