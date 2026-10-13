# Render the launch video: picture (Edge + ffmpeg), soundtrack (numpy/scipy via uv), mux, README preview GIF.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
node render.js video
uv run --no-project --with numpy --with scipy python music.py
ffmpeg -y -v error -i out/silent.mp4 -i out/music.wav -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart ../docs/launch.mp4
node render.js gif 11 23
Copy-Item out/preview.gif ../docs/preview.gif -Force
Write-Host "docs/launch.mp4 and docs/preview.gif updated"
