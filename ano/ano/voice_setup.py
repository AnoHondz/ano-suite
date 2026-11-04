"""Install the small offline English model from Vosk's official distribution."""
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

MODEL_NAME = 'vosk-model-small-en-us-0.15'
MODEL_URL = f'https://alphacephei.com/vosk/models/{MODEL_NAME}.zip'


def default_model_path():
    from .paths import data_directory
    data = data_directory()
    return Path(os.environ.get('ANO_VOSK_MODEL') or data / 'speech' / MODEL_NAME)


def model_ready(path):
    return all((Path(path) / file).is_file() for file in ('am/final.mdl', 'conf/model.conf', 'graph/HCLr.fst'))


def extract_model(archive, directory):
    root = Path(directory).resolve()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > 10000 or sum(item.file_size for item in members) > 512 * 1024 * 1024:
            raise ValueError('Unexpected model archive size.')
        for item in members:
            target = (root / item.filename).resolve()
            if not target.is_relative_to(root / MODEL_NAME) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unexpected model archive path.')
        package.extractall(root)
    model = root / MODEL_NAME
    if not model_ready(model):
        raise ValueError('The archive did not contain a complete Vosk model.')
    return model


def install_model(destination):
    destination = Path(destination).resolve()
    if model_ready(destination):
        return destination
    if destination.exists():
        raise ValueError('The model folder exists but is incomplete. Choose a new ANO_VOSK_MODEL folder.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ano-model-', dir=destination.parent) as directory:
        archive = Path(directory) / 'model.zip'
        with urlopen(MODEL_URL, timeout=30) as response, archive.open('wb') as output:
            source = urlparse(response.geturl())
            if source.scheme != 'https' or source.hostname != 'alphacephei.com':
                raise ValueError('Unexpected model download destination.')
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError('The model download exceeded its size limit.')
                output.write(chunk)
        extracted = extract_model(archive, directory)
        # Both paths are resolved within the explicitly selected model parent.
        if not extracted.resolve().is_relative_to(destination.parent):
            raise ValueError('Invalid model installation path.')
        extracted.rename(destination)
    return destination


if __name__ == '__main__':
    print('Installing the small English Vosk model from alphacephei.com...', flush=True)
    path = install_model(default_model_path())
    print(f'Offline voice model ready: {path}')
