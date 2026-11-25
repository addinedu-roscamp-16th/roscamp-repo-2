from setuptools import find_packages, setup

setup(
    name='service_bridge',
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/service_bridge']),
        ('share/service_bridge', ['package.xml']),
    ],
    install_requires=['setuptools'],
    tests_require=['pytest'],
    zip_safe=True,
    entry_points={'console_scripts': [
        'bridge = service_bridge.main:main',
        'setup_db = service_bridge.tools.setup_db:main',
        'submit_trip = service_bridge.tools.submit_trip:main',
        'fake_fleet = service_bridge.tools.fake_fleet:main',
    ]},
)
