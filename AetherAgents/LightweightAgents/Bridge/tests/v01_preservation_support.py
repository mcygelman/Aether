"""Synthetic public preservation fixture, without collecting full-edition switch tests."""
from contextlib import closing
import sqlite3
import uuid
import pytest
from aether.operations import distribution

@pytest.fixture
def installation(tmp_path):
    app, data, credentials, project = (tmp_path/n for n in ('app','data','credentials','project'))
    for root in (app, data, credentials, project):
        root.mkdir()
    (app/'code.py').write_text('print("synthetic")\n', encoding='utf-8')
    manifest = dict(schema=distribution.SCHEMA, python='3.10.8', dependencies=distribution.DEPENDENCIES,
                    source_commit='a'*40, uncommitted_changes=False, files=distribution.hashes(app))
    distribution.write_json(app/'bundle.json', manifest)
    config = distribution.layout(app, data, credentials, explicit_campaign_folders=True)
    distribution.write_json(app/'installation.json', config)
    distribution.write_json(data/'layout.json', config)
    distribution.write_json(data/'projects.json', {'projects':[dict(id='project', name='Fixture', root=str(project))]})
    guid = str(uuid.uuid4())
    identity = data/'homes'/'fixture'/'identity'
    identity.mkdir(parents=True)
    distribution.write_json(identity/'AGENT_ID.json', dict(guid=guid, readable_id='Synthetic_001', born='2026-10-12', aliases=[]))
    (data/'homes/fixture/memory').mkdir()
    (data/'homes/fixture/memory/own.txt').write_bytes(b'PRIVATE MEMORY synthetic owner words\r\n')
    (project/'obligations.json').write_text('{"pending":"effect-before-ack","retry":false}', encoding='utf-8')
    with closing(sqlite3.connect(project/'state.sqlite3')) as db:
        db.execute('CREATE TABLE obligations (id INTEGER PRIMARY KEY, state TEXT, cost INTEGER)')
        db.execute('INSERT INTO obligations VALUES (1,?,?)', ('unknown-do-not-retry', 1)); db.commit()
    return dict(app=app, data=data, credentials=credentials, project=project, guid=guid,
                mounts={'project':project}, owners={guid:'data/homes/fixture/identity/AGENT_ID.json'},
                credential_dependencies=[dict(provider='external-key-provider', reference='fixture-key-alias', purpose='unlock original protected mind')])
