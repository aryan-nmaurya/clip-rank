"""Explicit Pocket TTS installation. Ordinary generation never downloads assets."""
import json
from pathlib import Path
from importlib.resources import files
import requests
import yaml
from app.tts.pocket import PocketTTS,VOICES

REPOSITORY='kyutai/pocket-tts-without-voice-cloning'
MODEL_REVISION='e7205b6ee50e654a5ea19f0e9df2b0813b05e921'
TOKENIZER_REVISION='00eac05ed3d16bdc3f6b5d598874019c34a89214'
VOICE_REVISION='1e08e6a23401048648a9fdcfde2f89348215c2a7'


def download(filename,revision,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    partial=target.with_suffix(target.suffix+'.partial')
    url=f'https://huggingface.co/{REPOSITORY}/resolve/{revision}/{filename}'
    # Pinned public assets only. No authentication or voice-cloning consent flow.
    with requests.get(url,stream=True,timeout=(15,120)) as response:
        response.raise_for_status()
        with partial.open('wb') as handle:
            for chunk in response.iter_content(1024*1024): handle.write(chunk)
    partial.replace(target)
    print('Installed',target.name,target.stat().st_size,'bytes',flush=True)


def main():
    root=PocketTTS.directory().resolve();root.mkdir(parents=True,exist_ok=True)
    manifest_path=root/'manifest.json'
    # An incomplete install cannot be mistaken for ready or mix embedding versions.
    manifest_path.unlink(missing_ok=True)
    download('languages/english/model.safetensors',MODEL_REVISION,root/'model.safetensors')
    download('languages/english/tokenizer.json',TOKENIZER_REVISION,root/'tokenizer.json')
    voices={}
    for voice in VOICES:
        relative='voices/'+voice+'.safetensors'
        download('languages/english/embeddings/'+voice+'.safetensors',VOICE_REVISION,root/relative)
        voices[voice]=relative
    settings=yaml.safe_load((files('pocket_tts')/'config'/'english.yaml').read_text())
    settings['weights_path']=str(root/'model.safetensors')
    settings['weights_path_without_voice_cloning']=None
    settings['flow_lm']['lookup_table']['tokenizer_path']=str(root/'tokenizer.json')
    (root/'english.yaml').write_text(yaml.safe_dump(settings,sort_keys=False))
    manifest={'config':'english.yaml','voices':voices,'model_repository':REPOSITORY,'model_revision':MODEL_REVISION,
              'tokenizer_revision':TOKENIZER_REVISION,'voice_revision':VOICE_REVISION,'model_license':'CC BY 4.0'}
    manifest_path.write_text(json.dumps(manifest,indent=2))
    print(PocketTTS.status()['message'])


if __name__=='__main__': main()
