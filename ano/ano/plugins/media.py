"""Media keys: the same as pressing play/pause, next, or volume on a keyboard."""
import ctypes

from ..tools import ToolError

KEYS = {'play_pause': 0xB3, 'next': 0xB0, 'previous': 0xB1, 'stop': 0xB2,
        'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD}
KEYEVENTF_EXTENDEDKEY = 0x1
KEYEVENTF_KEYUP = 0x2


def press_key(code):
    user32 = ctypes.windll.user32
    user32.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY, 0)
    user32.keybd_event(code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


class Media:
    def __init__(self, press=press_key):
        self.press = press

    def definitions(self):
        return [{'name': 'media_control',
                 'description': 'Control whatever is playing (Spotify, YouTube, etc.) and the system volume. '
                                'Each volume step is about 2%. Cannot tell what is playing.',
                 'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                     'action': {'type': 'string', 'enum': sorted(KEYS)},
                     'steps': {'type': 'integer', 'description': 'Volume steps, 1 to 25, default 1'}},
                     'required': ['action']}}]

    def run(self, name, arguments):
        return self.media_control(**arguments)

    def media_control(self, action, steps=1):
        if action not in KEYS:
            raise ToolError('Unknown media action.')
        repeats = max(1, min(int(steps), 25)) if action.startswith('volume') else 1
        for _ in range(repeats):
            self.press(KEYS[action])
        label = action.replace('_', ' ')
        return f'Pressed {label}' + (f' {repeats} times.' if repeats > 1 else '.'), f'Media: {label}'
