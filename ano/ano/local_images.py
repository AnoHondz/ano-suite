"""Install and inspect Ano's pinned, CPU-only image runtime and SDXS weights.

Setup: python -m ano.local_images install
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

RELEASE = 'master-1015-e41fbcf'
RUNTIME_NAME = 'sd-master-e41fbcf-bin-win-cpu-x64.zip'
RUNTIME_URL = f'https://github.com/leejet/stable-diffusion.cpp/releases/download/{RELEASE}/{RUNTIME_NAME}'
RUNTIME_SHA256 = 'bfc02f16df7744296ecc7720503a2b633575490dd7d8b448aee035c1fc836bec'
MODEL_REVISION = '17ef024d73ebee1db9b0643f9c90454dae6c5772'
MODEL_URL = f'https://huggingface.co/akleine/sdxs-512/resolve/{MODEL_REVISION}/sdxs.safetensors'
MODEL_SHA256 = '6cca5bfd11b588cdfb4602018c7e623d24c95fdfdc5ab2d4b9e6978b3186980f'


def image_root():
    from .paths import data_directory
    return data_directory() / 'local-images'


def image_paths(root=None):
    root = Path(root) if root is not None else image_root()
    runtime = root / RELEASE
    matches = list(runtime.rglob('sd-cli.exe')) if runtime.is_dir() else []
    return (matches[0] if len(matches) == 1 else runtime / 'sd-cli.exe', root / 'sdxs.safetensors')


def ready(root=None):
    return all(path.is_file() for path in image_paths(root))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, path, expected, progress=print):
    """Verify a complete download before publishing it; never use partial weights."""
    path = Path(path)
    if path.exists():
        if file_hash(path) != expected:
            raise ValueError(f'{path.name} failed its checksum. Move it aside before installing again.')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as target:
            temporary = Path(target.name)
            digest = hashlib.sha256()
            received = next_report = 0
            request = urllib.request.Request(url, headers={'User-Agent': 'Ano-local-image-setup/1'})
            with urllib.request.urlopen(request, timeout=90) as response:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if received >= next_report:
                        progress(f'{path.name}: {received // (1024 * 1024)} MB downloaded')
                        next_report = received + 64 * 1024 * 1024
        if digest.hexdigest() != expected:
            raise ValueError(f'{path.name} failed its checksum; the download was not installed.')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install(root=None):
    if os.name != 'nt':
        raise ValueError('This installer provides the Windows x64 CPU runtime.')
    root = Path(root) if root is not None else image_root()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / RUNTIME_NAME
    download(RUNTIME_URL, archive, RUNTIME_SHA256, lambda text: print(text, flush=True))
    runtime = root / RELEASE
    if not image_paths(root)[0].is_file():
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            unpack = Path(temporary)
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    target = (unpack / member.filename).resolve()
                    if not target.is_relative_to(unpack.resolve()):
                        raise ValueError('The runtime archive contains an invalid path.')
                bundle.extractall(unpack)
            if len(list(unpack.rglob('sd-cli.exe'))) != 1:
                raise ValueError('The runtime archive did not contain one sd-cli.exe.')
            if runtime.exists():
                raise ValueError('An incomplete runtime folder exists. Move it aside before reinstalling.')
            shutil.copytree(unpack, runtime)
    download(MODEL_URL, root / 'sdxs.safetensors', MODEL_SHA256,
             lambda text: print(text, flush=True))
    (root / 'SOURCES.json').write_text(json.dumps({
        'runtime': RUNTIME_URL, 'runtime_sha256': RUNTIME_SHA256,
        'weights': MODEL_URL, 'weights_sha256': MODEL_SHA256,
        'model': 'SDXS-512-DreamShaper (converted by akleine)',
        'model_card': 'https://huggingface.co/IDKiro/sdxs-512-dreamshaper',
        'conversion_card': 'https://huggingface.co/akleine/sdxs-512',
        'declared_license': 'openrail++',
    }, indent=2), encoding='utf-8')
    print('Local image generation installed. Restart Ano, then say: generate image: a red fox in a forest')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'status'), default='status', nargs='?')
    args = parser.parse_args()
    if args.command == 'install':
        try:
            install()
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            parser.exit(1, f'Local image setup failed: {error}\n')
    else:
        print(json.dumps({'ready': ready(), 'paths': [str(path) for path in image_paths()]}, indent=2))


if __name__ == '__main__':
    main()
