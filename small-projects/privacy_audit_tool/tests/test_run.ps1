$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..").Path
py -m unittest discover -s "$PSScriptRoot" -v
