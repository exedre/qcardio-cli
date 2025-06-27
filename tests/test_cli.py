import pytest
from qcardio.cli import QardioShell

def test_load_arm_plugin():
    shell = QardioShell('arm', None, None)
    assert hasattr(shell.device, 'discover')
