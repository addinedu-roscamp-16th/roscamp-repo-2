from glob import glob
from setuptools import setup

name = 'jetcobot_dual_bridge'
# The bridges run as plain scripts with the Pi's pymycobot interpreter
# (launch argument `python`), so they are installed as data, not as a module.
setup(
    name=name, version='0.2.0', packages=[],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + name]),
        ('share/' + name, ['package.xml', 'README.md']),
        ('share/' + name + '/scripts', glob('scripts/*.py')),
        ('share/' + name + '/sdk_patch', glob('sdk_patch/*.py') + glob('sdk_patch/*.patch')),
        ('share/' + name + '/config', glob('config/*.yaml')),
        ('share/' + name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'], zip_safe=True,
    maintainer='ane', maintainer_email='ane@example.com',
    description='Pi side SDK bridges for the dual JetCobot setup',
    license='Apache-2.0',
)
