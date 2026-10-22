from __future__ import annotations

import sqlite3

import pytest

from photo_search import photo_collections
from photo_search.catalog.errors import CatalogError
from photo_search.tagging import search as tags
from unit.test_tagging_search import tagged_env as tagged_env


def bump_revision(env, processor):
    with sqlite3.connect(env.catalog.db_path) as connection:
        connection.execute('UPDATE machine_tag_models SET revision=revision+1 WHERE processor_id=?', (processor,))


@pytest.mark.parametrize('action', ['page', 'time'])
def test_revision_commit_between_resolve_and_response_is_refused(tagged_env, monkeypatch, action):
    env, processor = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {'request_id': 'review-race', 'original_query': '猫', 'page_size': 1, 'category_rank': tags.VERSION}
        first_response = await client.post('/api/v1/search/place-aware', headers=headers, json=body)
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        original = tags.resolve_tag_query
        triggered = False
        def racing_resolve(*args, **kwargs):
            nonlocal triggered
            resolved = original(*args, **kwargs)
            if not triggered:
                bump_revision(env, processor)
                triggered = True
            return resolved
        monkeypatch.setattr(tags, 'resolve_tag_query', racing_resolve)
        if action == 'page':
            response = await client.post('/api/v1/search/place-aware', headers=headers, json={
                **body, 'intent_plan': first['intent_plan'], 'search_session_id': first['search_session_id'],
                'cursor': first['next_cursor']})
        else:
            response = await client.get(f"/api/v1/search/sessions/{first['search_session_id']}/time?limit=1")
        assert triggered
        assert response.status_code == 422, response.text
        assert response.json()['code'] == 'INVALID_CURSOR'
    env.scenario(scenario)


@pytest.mark.parametrize('action', ['create', 'append'])
def test_revision_commit_after_source_validation_before_collection_write_is_refused(tagged_env, monkeypatch, action):
    env, processor = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        first_response = await client.post('/api/v1/search/place-aware', headers=headers, json={
            'request_id': 'review-race', 'original_query': '猫', 'page_size': 1, 'category_rank': tags.VERSION})
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        if action == 'create':
            original = photo_collections.create_collection
            def race(*args, **kwargs):
                bump_revision(env, processor)
                return original(*args, **kwargs)
            monkeypatch.setattr(photo_collections, 'create_collection', race)
            response = await client.post('/api/v1/photo-collections/from-search', headers=headers, json={
                'name': 'Race save', 'create_request_id': 'race-save', 'mode': 'ranked_candidates',
                'source_token': first['photo_collection_source_token']})
        else:
            created = photo_collections.create_collection(env.catalog, 'target', 'review-target')
            target = created['collection']
            original = photo_collections.edit_members
            def race(*args, **kwargs):
                bump_revision(env, processor)
                return original(*args, **kwargs)
            monkeypatch.setattr(photo_collections, 'edit_members', race)
            response = await client.post(f"/api/v1/photo-collections/{target['collection_id']}/members/from-search", headers=headers, json={
                'request_id': 'race-append', 'instance_token': target['instance_token'],
                'expected_revision': target['revision'], 'mode': 'ranked_candidates',
                'source_token': first['photo_collection_source_token']})
        assert response.status_code in (409, 422), response.text
        assert response.json()['code'] == 'INVALID_CURSOR'
        if action == 'create':
            assert env.catalog.read_one('SELECT COUNT(*) FROM photo_collections')[0] == 0
        else:
            unchanged = env.catalog.read_one(
                'SELECT revision,member_count FROM photo_collections WHERE collection_id=?',
                (target['collection_id'],))
            assert tuple(unchanged) == (target['revision'], 0)
        assert env.catalog.read_one('SELECT COUNT(*) FROM photo_collection_members')[0] == 0
        assert env.catalog.read_one('SELECT COUNT(*) FROM photo_collection_search_appends')[0] == 0
    env.scenario(scenario)


@pytest.mark.parametrize('changes', [
    {'processor_id': None}, {'processor_id': ''}, {'revision': None},
    {'revision': True}, {'revision': -1}, {'identity': 'another-processor:3'},
])
def test_collection_revision_binding_refuses_incomplete_or_changed_identity(changes):
    summary = tags.TagQuery('processor:3', '0', 'processor', 3, '猫', 1).summary(applied=True)
    assert tags.revision_binding(summary) == ('processor', 3)
    with pytest.raises(CatalogError) as error:
        tags.revision_binding({**summary, **changes})
    assert error.value.code == 'INVALID_CURSOR'
    assert error.value.details['reason'] == 'CATEGORY_RANK_CHANGED'
    assert tags.revision_binding(None) is None
    assert tags.revision_binding({'requested': 'off'}) is None


@pytest.mark.parametrize('action', ['page', 'time'])
def test_page_linearizes_at_tag_snapshot_and_next_request_refuses(tagged_env, monkeypatch, action):
    from photo_search.tagging import store

    env, processor = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {'request_id': 'review-snapshot', 'original_query': '猫', 'page_size': 1, 'category_rank': tags.VERSION}
        first_response = await client.post('/api/v1/search/place-aware', headers=headers, json=body)
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        original = store.require_revision
        triggered = False
        def publish_after_snapshot_read(connection, expected):
            nonlocal triggered
            original(connection, expected)
            if connection.in_transaction and not triggered:
                bump_revision(env, processor)
                triggered = True
        monkeypatch.setattr(store, 'require_revision', publish_after_snapshot_read)
        async def fetch():
            if action == 'page':
                return await client.post('/api/v1/search/place-aware', headers=headers, json={
                    **body, 'intent_plan': first['intent_plan'], 'search_session_id': first['search_session_id'],
                    'cursor': first['next_cursor']})
            return await client.get(f"/api/v1/search/sessions/{first['search_session_id']}/time?limit=1")
        response = await fetch()
        assert triggered and response.status_code == 200, response.text
        assert response.json()['category_rank'] == first['category_rank']
        stale = await fetch()
        assert stale.status_code == 422 and stale.json()['code'] == 'INVALID_CURSOR', stale.text
    env.scenario(scenario)


@pytest.mark.parametrize('action', ['create', 'append'])
def test_committed_collection_request_replays_after_tag_publication(tagged_env, action):
    env, processor = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        first_response = await client.post('/api/v1/search/place-aware', headers=headers, json={
            'request_id': 'review-replay', 'original_query': '猫', 'page_size': 1, 'category_rank': tags.VERSION})
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        if action == 'create':
            endpoint = '/api/v1/photo-collections/from-search'
            payload = {'name': 'Replay save', 'create_request_id': 'replay-save', 'mode': 'ranked_candidates',
                       'source_token': first['photo_collection_source_token']}
        else:
            target = photo_collections.create_collection(env.catalog, 'target', 'review-target')['collection']
            endpoint = f"/api/v1/photo-collections/{target['collection_id']}/members/from-search"
            payload = {'request_id': 'replay-append', 'instance_token': target['instance_token'],
                       'expected_revision': target['revision'], 'mode': 'ranked_candidates',
                       'source_token': first['photo_collection_source_token']}
        response = await client.post(endpoint, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        initial = response.json()
        bump_revision(env, processor)
        replay = await client.post(endpoint, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()['reused'] is True, replay.text
        assert replay.json()['collection'] == initial['collection']
        rows = env.catalog.read_all('SELECT asset_id FROM photo_collection_members WHERE collection_id=?',
                                    (initial['collection']['collection_id'],))
        assert len(rows) == 3 and {int(row[0]) for row in rows} == set(env.assets.values())
    env.scenario(scenario)
