"""Image generation with OpenAI. Images are saved under Pictures\\Ano and opened for the user."""
import base64
from datetime import datetime
import os
from pathlib import Path
import re

from PySide6.QtCore import QStandardPaths

from ..tools import ToolError

IMAGE_MODEL = 'gpt-image-2'
SIZES = {'square': '1024x1024', 'portrait': '1024x1536', 'landscape': '1536x1024'}


def pictures_folder():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)) / 'Ano'


class Images:
    def __init__(self, key, folder=pictures_folder, open_path=getattr(os, 'startfile', None), connect=None):
        self.key = key
        self.folder = folder
        self.open_path = open_path
        self.connect = connect

    def definitions(self):
        return [{'name': 'generate_image',
                 'description': 'Create a picture with OpenAI from a detailed description (subject, style, colours, '
                                'composition, any text to include). It is saved in Pictures\\Ano and opened for the '
                                'user. Takes up to a minute.',
                 'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                     'prompt': {'type': 'string'},
                     'shape': {'type': 'string', 'enum': sorted(SIZES), 'description': 'Default square'}},
                     'required': ['prompt']}}]

    def run(self, name, arguments):
        return self.generate_image(**arguments)

    def generate_image(self, prompt, shape='square'):
        if not prompt.strip():
            raise ToolError('Describe the picture to create.')
        if self.connect:
            api = self.connect()
        else:
            from openai import OpenAI
            api = OpenAI(api_key=self.key, timeout=180.0, max_retries=0)
        try:
            result = api.images.generate(model=IMAGE_MODEL, prompt=prompt, size=SIZES.get(shape, SIZES['square']),
                                         quality='medium', n=1)
        except Exception as error:
            if getattr(error, 'code', None) == 'moderation_blocked':
                raise ToolError('OpenAI declined to make that image. Try a different description.') from None
            if getattr(error, 'status_code', None) == 400:
                raise ToolError(f'OpenAI rejected the request: {getattr(error, "message", "")[:200]}') from None
            raise ToolError('OpenAI could not create the image. Check the internet connection and try again.') from None
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r'[^a-z0-9]+', '-', prompt.casefold())[:40].strip('-') or 'image'
        path = folder / f'{datetime.now():%Y-%m-%d %H%M%S} {slug}.png'
        path.write_bytes(base64.b64decode(result.data[0].b64_json))
        if self.open_path:
            self.open_path(str(path))
        return f'Saved {path}' + (' and opened it.' if self.open_path else '.'), f'Created an image: {path.name}'
