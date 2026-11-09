"""A mind's test runs get the operating system's bootstrap variables only: the server's environment may hold a
provider key, and the tests run code a model wrote. Synthetic values only; no model is called.
"""
import os

import pytest

from aether.atelier import turn

PLANTED = 'planted-fake-value-for-this-test'


class Mind:
    roots = {}
    allowed_tools = None

    def technical(self):
        return False


def test_only_bootstrap_variables_are_passed_on():
    environ = {'AETHER_API_KEY': PLANTED, 'DEEPSEEK_API_KEY': PLANTED, 'OPENAI_API_KEY': PLANTED,
               'AWS_SECRET_ACCESS_KEY': PLANTED, 'PYTHONPATH': r'D:\private', 'SystemRoot': r'C:\Windows',
               'Path': r'C:\Windows\System32', 'TEMP': r'C:\Temp', 'PYTHONUTF8': '1'}
    assert turn.test_environment(environ) == {'SystemRoot': r'C:\Windows', 'Path': r'C:\Windows\System32',
                                              'TEMP': r'C:\Temp', 'PYTHONUTF8': '1'}


@pytest.mark.parametrize('runner', ['pytest', 'unittest'])
def test_a_minds_tests_cannot_read_the_servers_key(tmp_path, monkeypatch, runner):
    monkeypatch.setenv('AETHER_API_KEY', PLANTED)
    if runner == 'unittest':                              # the installed runtime ships without pytest
        monkeypatch.setattr(turn.importlib.util, 'find_spec', lambda name: None)
    root = tmp_path / 'commission'
    tests = root / 'repository/tests'
    tests.mkdir(parents=True)
    (tests / 'test_env.py').write_text(
        'import os, unittest\n\n\nclass T(unittest.TestCase):\n'
        '    def test_no_key(self):\n        self.assertNotIn("AETHER_API_KEY", os.environ)\n', encoding='utf-8')
    out = turn.Tools(Mind(), root).call('run_tests', {'path': 'repository/tests'})
    assert f'[{runner} from repository, exit code 0]' in out, out
    assert PLANTED not in out
    assert os.environ['AETHER_API_KEY'] == PLANTED        # the server's own environment is not changed
