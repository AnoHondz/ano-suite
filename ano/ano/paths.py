"""One data location across packaged-development and normal Windows launches."""
import os
from pathlib import Path


def shared_state_directory():
    from PySide6.QtCore import QStandardPaths
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents or Path.home() / 'Documents') / 'AnoWorkspace' / '.ano-state'


def data_directory():
    if os.environ.get('ANO_DATA_DIR'):
        return Path(os.environ['ANO_DATA_DIR'])
    shared = shared_state_directory()
    # Only adopt a fully prepared migration. Never create an empty replacement
    # that makes history appear lost merely because another launcher was used.
    if (shared / 'migration-complete.json').is_file() and (shared / 'memory.sqlite3').is_file():
        return shared
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'Ano' / 'Ano'
