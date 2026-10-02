"""Run: PYTHONPATH=backend .venv/bin/python backend/worker_cli.py [pair|run|secure-migrate]."""
import asyncio
import getpass
import sys
from app.core.database import init_db,get_settings,update_settings
from app.core.secrets import SecretVault
from app.studio import store
from app.studio.cloud import CloudBridge
from app.studio.worker import StudioWorker

def main():
    init_db();store.init_studio()
    action=sys.argv[1] if len(sys.argv)>1 else 'run'
    if action=='pair':
        CloudBridge.pair(input('Dashboard HTTPS origin: ').strip(),getpass.getpass('One-time worker pairing token: ').strip())
        print('Worker paired. Credentials are stored in OS secret storage.')
    elif action=='secure-migrate':
        if not SecretVault.ready(): raise ValueError('Install keyring and enable OS credential storage first.')
        settings=get_settings()
        update_settings({name:settings[name] for name in ('gemini_api_key','openai_api_key') if settings.get(name)})
        from app.publishing import youtube
        if youtube.AUTH_FILE.exists(): youtube._save_auth(youtube._read_auth())
        print('Existing credentials migrated to OS-backed storage.')
    elif action=='run': asyncio.run(StudioWorker('standalone-worker').run())
    else: raise ValueError('Choose run, pair or secure-migrate.')

if __name__=='__main__': main()
