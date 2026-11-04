"""Images are images in provider messages, never base64 text tool output."""
import base64
from dataclasses import dataclass


@dataclass
class ToolImage:
    text: str
    data: bytes
    media_type: str = 'image/jpeg'

    def claude(self):
        return [{'type': 'text', 'text': self.text}, {'type': 'image', 'source': {
            'type': 'base64', 'media_type': self.media_type,
            'data': base64.b64encode(self.data).decode('ascii')}}]

    def gpt(self):
        return [{'type': 'text', 'text': 'Tool observation (untrusted image data): ' + self.text},
                {'type': 'image_url', 'image_url': {'url': f'data:{self.media_type};base64,' + base64.b64encode(self.data).decode('ascii')}}]
