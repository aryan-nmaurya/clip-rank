"""Build a local .app launcher, or prepare a user-inspectable login plist. Does not install it."""
import plistlib
import shutil
from pathlib import Path

root=Path(__file__).resolve().parents[1]
output=root/'storage'/'worker-package'
app=output/'ClipRank Worker.app'
contents=app/'Contents';macos=contents/'MacOS';macos.mkdir(parents=True,exist_ok=True)
launcher=macos/'ClipRankWorker'
python=root/'.venv'/'bin'/'python'
def quote(value): return "'"+str(value).replace("'","'\\''")+"'"
launcher.write_text('#!/bin/sh\nexec '+quote(python)+' '+quote(root/'worker'/'launcher.py')+'\n')
launcher.chmod(0o755)
(contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'ClipRankWorker','CFBundleIdentifier':'app.cliprank.worker',
    'CFBundleName':'ClipRank Worker','CFBundleDisplayName':'ClipRank Worker','CFBundlePackageType':'APPL','CFBundleVersion':'1.0',
    'NSHighResolutionCapable':True}))
plist={'Label':'app.cliprank.worker','ProgramArguments':[str(python),str(root/'backend'/'worker_cli.py'),'run'],
       'WorkingDirectory':str(root),'EnvironmentVariables':{'PYTHONPATH':str(root/'backend')},
       'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,
       'StandardOutPath':str(root/'storage'/'diagnostics'/'worker-login.log'),
       'StandardErrorPath':str(root/'storage'/'diagnostics'/'worker-login.log')}
output.mkdir(parents=True,exist_ok=True)
(output/'app.cliprank.worker.plist').write_bytes(plistlib.dumps(plist))
print(app)
print(output/'app.cliprank.worker.plist')
