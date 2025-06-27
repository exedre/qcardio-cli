from setuptools import setup, find_packages
setup(
    name="qcardio",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "bleak",
        "pyyaml",
    ],
    entry_points={"console_scripts":["qardio=qcardio.cli:main"]},
)
