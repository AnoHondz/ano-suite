"""Free local SDXS image generation using a short-lived CPU subprocess."""
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from PySide6.QtGui import QImage

from ..local_images import image_paths, image_root, ready
from ..tools import ToolError
from ..inference_gate import memory_pressure
from .images import pictures_folder

SIZES = {'square': (512, 512), 'portrait': (384, 512), 'landscape': (512, 384)}


class LocalImages:
    def __init__(self, root=image_root, folder=pictures_folder, allowed=lambda: True,
                 open_path=getattr(os, 'startfile', None), launch=subprocess.Popen, timeout=600, sample=memory_pressure):
        self.root, self.folder, self.allowed = root, folder, allowed
        self.open_path, self.launch, self.timeout = open_path, launch, timeout
        self._busy = threading.Lock()
        self.sample = sample

    def definitions(self):
        return [{'name': 'generate_image',
                 'description': 'Generate a picture locally with SDXS, without API fees or uploading the prompt. '
                                'Use a short visual description. Saves to Pictures\\Ano. CPU generation may take minutes; '
                                'text, faces and fine details can be imperfect. Requires installed local image files.',
                 'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                     'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                     'shape': {'type': 'string', 'enum': sorted(SIZES)}}, 'required': ['prompt']}}]

    def run(self, name, arguments):
        return self.generate_image(**arguments)

    def generate_image(self, prompt, shape='square'):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
            raise ToolError('Describe the picture in 1 to 2,000 characters; short visual prompts work best.')
        if shape not in SIZES:
            raise ToolError('Choose square, portrait, or landscape.')
        if not self.allowed():
            raise ToolError('Image generation stopped because Ano is paused, locked, or in private login.')
        root = Path(self.root())
        if not ready(root):
            raise ToolError('Local image files are not installed. Run: python -m ano.local_images install. '
                            'No paid image service was called.')
        pressure=self.sample()
        if pressure and (pressure[0]>=92 or pressure[1]<1024**3):
            raise ToolError('Image generation is waiting for free RAM. Ano can release idle local models automatically; '
                            'try again after cleanup. No app was closed or paid image service called.')
        if not self._busy.acquire(blocking=False):
            raise ToolError('Ano is already generating a local image. Wait for it to finish.')
        try:
            return self._generate(root, prompt.strip(), shape)
        finally:
            self._busy.release()

    def _generate(self, root, prompt, shape):
        executable, model = image_paths(root)
        width, height = SIZES[shape]
        # Keep prompts/runtime logs temporary; leave only the requested final image.
        with tempfile.TemporaryDirectory(prefix='image-job-', dir=root) as directory:
            job = Path(directory)
            command = [str(executable), '-m', str(model), '-p', prompt,
                       '-o', str(job / 'output.png'), '--steps', '1', '--cfg-scale', '1',
                       '-W', str(width), '-H', str(height), '-t', '2', '--seed', '-1']
            with (job / 'runtime.log').open('wb') as log:
                try:
                    process = self.launch(command, stdin=subprocess.DEVNULL, stdout=log,
                                          stderr=subprocess.STDOUT,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except OSError:
                    raise ToolError('The local image runtime could not start. Reinstall its CPU runtime.') from None
                deadline = time.monotonic() + self.timeout
                try:
                    while process.poll() is None:
                        if not self.allowed():
                            raise ToolError('Local image generation was cancelled because Ano was paused or locked.')
                        if time.monotonic() >= deadline:
                            raise ToolError('Local image generation timed out. No paid fallback was used.')
                        time.sleep(0.2)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            if process.returncode != 0:
                raise ToolError('The local image model could not finish. Close unused apps to free memory and try again. '
                                'No paid image service was called.')
            if not self.allowed():
                raise ToolError('Local image generation was cancelled; no image was saved.')
            images = list(job.glob('*.png'))
            if len(images) != 1 or QImage(str(images[0])).isNull():
                raise ToolError('The local image runtime returned no readable PNG. No completed image was reported.')
            destination = Path(self.folder())
            destination.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r'[^a-z0-9]+', '-', prompt.casefold())[:40].strip('-') or 'image'
            target = destination / f'{datetime.now():%Y-%m-%d %H%M%S} {slug}-{uuid.uuid4().hex[:8]}.png'
            with target.open('xb') as output, images[0].open('rb') as source:
                shutil.copyfileobj(source, output)
        opened = False
        if self.open_path:
            try:
                self.open_path(str(target))
                opened = True
            except OSError:
                pass  # The image was still generated and saved successfully.
        return (f'Saved {target}' + (' and opened it.' if opened else '.') +
                ' Generated locally with SDXS; no API credits used.'), f'Created a local image: {target.name}'
