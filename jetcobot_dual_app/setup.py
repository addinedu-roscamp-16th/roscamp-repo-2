import os
from glob import glob
from setuptools import setup

name = 'jetcobot_dual_app'
KEEP = ('.py', '.json', '.npz', '.md')


def tree(src, dst):
    """Install scripts/ with its sub-folders; the scripts import siblings."""
    out = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'sam_yaw_output')]
        keep = [os.path.join(root, f) for f in files if f.endswith(KEEP)]
        if keep:
            out.append((os.path.join(dst, os.path.relpath(root, src)), keep))
    return out


setup(
    name=name, version='0.2.0', packages=[],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + name]),
        ('share/' + name, ['package.xml', 'README.md']),
        ('share/' + name + '/launch', glob('launch/*.launch.py')),
    ] + tree('scripts', 'share/' + name + '/scripts'),
    install_requires=['setuptools'], zip_safe=True,
    maintainer='ane', maintainer_email='ane@example.com',
    description='Dual JetCobot pick-and-place app',
    license='Apache-2.0',
)
