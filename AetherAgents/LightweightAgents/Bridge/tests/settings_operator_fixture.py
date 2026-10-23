"""Synthetic registered identity for settings tests; no real operator lookup."""
import json

from aether.roadmaps.operators import reader

OPERATOR_ID = '74f40e81-3c82-4620-9691-e0ddcbfc49f7'
OPERATOR = {'id': OPERATOR_ID, 'display_name': 'Fixture Operator'}


def operator_registry(tmp_path):
    path = tmp_path / 'settings-operators.json'
    if not path.exists():
        path.write_text(json.dumps({'active': '', 'projects': [],
            'local_operator': {**OPERATOR, 'revision': 1}}), encoding='utf-8')
    return path


def operator_options(tmp_path):
    """Store keywords: the registry lock path and its injected lookup."""
    path = operator_registry(tmp_path)
    return {'operator_registry_path': path, 'operators': reader(path)}
