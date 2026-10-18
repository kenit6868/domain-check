"""Keep an existing unpacked Chrome install working at its original path.

Copies only versioned runtime assets from FE. Does not install/reload Chrome,
change its profile or remove files from the compatibility directory.
"""
from pathlib import Path
import shutil


def sync(root=None):
    root = (Path(root) if root else Path(__file__).resolve().parents[2]).resolve()
    source = root / 'frontend' / 'extensions' / 'cloudflare-profile-worker'
    destination = root / 'chrome_extension' / 'cloudflare-profile-worker'
    count = 0
    for entry in sorted(source.rglob('*')):
        if not entry.is_file() or entry.suffix not in {'.js', '.json', '.html', '.css', '.png'}:
            continue
        relative = entry.relative_to(source)
        target = destination / relative
        if entry.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Extension path escapes its source/destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry, target)
        count += 1
    return count


if __name__ == '__main__':
    print(f'Synced {sync()} extension assets. Chrome profile was not modified.')
