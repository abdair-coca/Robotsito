import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest


class DummySerial:
    def cmd_estado(self, *a, **k): pass
    def cmd_servo(self, *a, **k): pass
    def cmd_siguiendo(self, *a, **k): pass
    def cmd_motor(self, *a, **k): pass


@pytest.fixture
def dummy_serial():
    return DummySerial()
