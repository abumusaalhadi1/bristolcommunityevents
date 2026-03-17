@echo off
setlocal
cd /d "C:\Users\Abu Musa Al Hadi\Desktop\Bristol Community Events - Flask\my_flask_app"
start "" "http://127.0.0.1:5001/"
call .\venv312\Scripts\python.exe app.py
pause
