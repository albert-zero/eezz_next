import importlib.resources
import shutil
from   pathlib import Path

src_dir  = importlib.resources.files('eezz') / 'webroot'
dest_dir = Path('webroot')

if not dest_dir.exists():
    shutil.copytree(str(src_dir), dest_dir)
