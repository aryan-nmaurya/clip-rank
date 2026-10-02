"""Small macOS worker window. No hidden login installation or external command execution."""
import os
from pathlib import Path
import subprocess
import sys
import socket
import tkinter as tk
from tkinter import messagebox
import webbrowser

root=Path(os.environ.get('CLIPRANK_PROJECT_ROOT',Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0,str(root/'backend'))
from app.core.database import init_db
from app.studio import store
init_db();store.init_studio()
window=tk.Tk();window.title('ClipRank Worker');window.geometry('440x370');window.configure(bg='#faf9f6')
process=None

def start():
    global process
    if process and process.poll() is None: return
    # The API owns an embedded durable worker and serves the local dashboard.
    # Reuse an already running studio instead of starting a duplicate listener.
    try:
        with socket.create_connection(('127.0.0.1',8000),timeout=.5): return
    except OSError: pass
    python=root/'.venv'/'bin'/'python'
    if not python.is_file():
        messagebox.showerror('Worker runtime missing','Run ClipRank setup before starting the worker.');return
    logs=root/'storage'/'diagnostics';logs.mkdir(parents=True,exist_ok=True)
    with (logs/'worker.log').open('a') as log:
        process=subprocess.Popen([str(python),str(root/'backend'/'run.py')],cwd=root,
            env={**os.environ,'PYTHONPATH':str(root/'backend')},stdout=log,stderr=log)

def pause():
    p=store.profile();p.enabled=False;store.save_profile(p)

def resume():
    p=store.profile();p.enabled=True;store.save_profile(p);start()

def close():
    if process and process.poll() is None:
        if not messagebox.askyesno('Stop worker?','Closing the worker stops local processing. Checkpoints will resume when it starts again.'): return
        process.terminate()
    window.destroy()

tk.Label(window,text='ClipRank Worker',font=('Helvetica',22,'bold'),bg='#faf9f6').pack(pady=(28,12))
status=tk.StringVar();tk.Label(window,textvariable=status,font=('Helvetica',12),bg='#faf9f6',justify='left').pack(pady=12)
controls=tk.Frame(window,bg='#faf9f6');controls.pack(pady=10)
tk.Button(controls,text='Pause Autopilot',command=pause).pack(side='left',padx=8)
tk.Button(controls,text='Resume',command=resume).pack(side='left',padx=8)
tk.Button(window,text='Open Dashboard',command=lambda:webbrowser.open('http://127.0.0.1:8000')).pack(pady=12)
tk.Label(window,text='Keep your Mac awake for scheduled production.',font=('Helvetica',10),fg='#71717a',bg='#faf9f6').pack(pady=10)

def refresh():
    state=store.worker_status();profile=store.profile()
    status.set(('● Online' if state['online'] else '○ Offline')+(' · Paused' if not profile.enabled else ' · Running')+
               f"\nQueue: {state.get('queue',0)}\nCurrent job: {state.get('current_job') or 'Idle'}"+
               f"\nCPU load: {state.get('cpu_load','—')}\nRAM: {state.get('process_peak_ram_mb','—')} MB\nFree disk: {state.get('disk_free_gb','—')} GB")
    window.after(3000,refresh)

window.protocol('WM_DELETE_WINDOW',close);start();refresh();window.mainloop()
