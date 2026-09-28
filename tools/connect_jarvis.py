"""Migrate explicitly supplied Jarvis launcher settings after live adapter checks.

No Jarvis code or running tasks are modified. Local paths and receipts are never
part of the public registry example or Git repository.
"""
import argparse
import copy
import importlib.util
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--launcher-config',required=True)
    parser.add_argument('--adapter-module',required=True)
    parser.add_argument('--backup-root',required=True)
    parser.add_argument('--port',type=int,default=4105)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    path=Path(args.launcher_config).resolve()
    original=path.read_bytes(); cfg=json.loads(original)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base=f'http://127.0.0.1:{args.port}'
    with opener.open(base+'/healthz',timeout=5) as response: health=json.load(response)
    if health.get('implementation')!='keyharbor' or not health.get('ready'):
        raise ValueError('KeyHarbor compatibility listener is not ready')
    models=health.get('models',[])
    if not models: raise ValueError('No managed models advertised')
    spec=importlib.util.spec_from_file_location('keyharbor_jarvis_adapter_check',args.adapter_module)
    adapter=importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    settings=copy.deepcopy(cfg.get('jarvis_connection',{}))
    shell=shutil.which('pwsh') or str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
    settings.setdefault('connections',{})['keyharbor']={
        'base_url':base+'/v1','health_url':base+'/healthz','credential_ref':'dpapi:keyharbor',
        'start_command':[shell,'-File',str(root/'tools/start.ps1'),'-WithWatchdog','-WithLegacyBridge'],
        'log_path':str(root/'runtime/jarvis-start.log'),
    }
    settings['enabled']=True
    settings['startup_timeout_seconds']=max(settings.get('startup_timeout_seconds',10),60)
    for model in models: settings.setdefault('model_connections',{})[model]='keyharbor'
    # Exercise the exact installed adapter, including its schema and live health checks.
    for model in models:
        if adapter.route_for_model(settings,model)!=base+'/v1':
            raise ValueError('Installed adapter cannot route a managed model; no changes applied')
    if adapter.route_for_model(settings,'official-native-check')!=adapter.OFFICIAL_BASE_URL:
        raise ValueError('Native route changed; no changes applied')
    cfg['jarvis_connection']=settings
    receipt={'applied':False,'managed_models':len(models),'base_url':base+'/v1',
             'native_route':'direct_official','existing_processes_restarted':False,
             'scope':'new task hosts loading this launcher configuration; existing sessions are unchanged'}
    if not args.apply:
        print(json.dumps(receipt));return
    if json.loads(original)==cfg:
        receipt.update(applied=True,changed=False);print(json.dumps(receipt));return
    folder=Path(args.backup_root)/(time.strftime('%Y%m%d-%H%M%S')+'-keyharbor-jarvis')
    folder.mkdir(parents=True,exist_ok=False)
    shutil.copy2(path,folder/path.name)
    (folder/'MANIFEST.md').write_text(f'# KeyHarbor route migration\n\nSource: {path}\n\nBackup: {folder/path.name}\n\n'
        'Reason: send new managed-model tasks through KeyHarbor; retain direct official routing.\n\n'
        'Restore: compare later changes, then copy the backed-up launcher configuration to its source.\n'
        '\nNo credentials, login files, databases, or runtime state were copied.\n',encoding='utf-8')
    if path.read_bytes()!=original: raise ValueError('Configuration changed concurrently; abort')
    temp=path.with_name(path.name+'.keyharbor-'+str(time.time_ns())+'.tmp')
    temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,path)
    if json.loads(path.read_bytes())!=cfg: raise ValueError('Configuration readback mismatch')
    receipt.update(applied=True,changed=True,backup=str(folder))
    (root/'runtime/jarvis-migration.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'applied':False,'error_type':type(exc).__name__}));raise SystemExit(1)
