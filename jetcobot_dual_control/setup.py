from glob import glob
from setuptools import find_packages, setup

name = 'jetcobot_dual_control'
setup(
    name=name, version='0.1.0', packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + name]),
        ('share/' + name, ['package.xml', 'README.md']),
        ('share/' + name + '/config', glob('config/*')),
        ('share/' + name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools', 'PyYAML'], zip_safe=True,
    maintainer='ane', maintainer_email='ane@example.com',
    description='Dual JetCobot measured-state planning and separate SDK bridges',
    license='Apache-2.0',
    entry_points={'console_scripts': [
        'state_aggregator = jetcobot_dual_control.state_aggregator:main',
        'execution_gate = jetcobot_dual_control.execution_gate:main',
        'tin_case_scene = jetcobot_dual_control.tin_case_scene:main',
        'pick_object_scene = jetcobot_dual_control.pick_object_scene:main',
    ]},
)
