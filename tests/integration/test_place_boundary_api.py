from __future__ import annotations

import json

from shapely.geometry import box, mapping


def test_boundary_import_authentication_hierarchy_and_validation(api_env):
    payload = {
        'format': 'photo-search-boundaries.v1',
        'metadata': dict(namespace='api-test', version='1', crs='EPSG:4326',
                         source='https://example.org/data', license='CC0', coverage='test',
                         boundary_year='2026', layer='administrative'),
        'features': [dict(id='country', kind='country', name='中国'),
                     dict(id='province', kind='province', name='浙江', parent='country'),
                     dict(id='city', kind='city', name='杭州', parent='province'),
                     dict(id='district', kind='district', name='西湖区', parent='city', geometry=mapping(box(120,30,121,31)))],
    }
    async def scenario(client):
        denied = await client.get('/api/v1/places/boundaries')
        assert denied.status_code == 401
        await api_env.authenticate(client)
        denied = await client.post('/api/v1/places/boundaries', json=payload)
        assert denied.status_code == 403
        accepted = await client.post('/api/v1/places/boundaries', json=payload, headers=api_env.post_headers(api_env.csrf_token))
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['packages'][0]['feature_count'] == 1
        roots = await client.get('/api/v1/places/browse', params={'show_empty':'true'})
        assert roots.status_code == 200, roots.text
        assert [p['canonical_name'] for p in roots.json()['places']] == ['中国']
        parent = roots.json()['places'][0]['place_id']
        children = await client.get('/api/v1/places/browse', params={'parent':parent,'show_empty':'true'})
        assert [p['kind'] for p in children.json()['places']] == ['province']
        assert children.json()['ancestors'] == []
        assert 'latitude' not in json.dumps(children.json())
        payload['metadata']['crs'] = 'GCJ-02'
        invalid = await client.post('/api/v1/places/boundaries', json=payload, headers=api_env.post_headers(api_env.csrf_token))
        assert invalid.status_code == 422, invalid.text
    api_env.scenario(scenario)
