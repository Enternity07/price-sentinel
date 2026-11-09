"""使用上游真实应用、真实数据存储及 CSRF；仅替换外部采集队列。"""

import re
import time
from uuid import uuid4

import pytest


@pytest.fixture(scope='module')
def application(tmp_path_factory):
    from changedetectionio.store import ChangeDetectionStore
    from changedetectionio.flask_app import changedetection_app
    folder = str(tmp_path_factory.mktemp('price-sentinel'))
    store = ChangeDetectionStore(datastore_path=folder, include_default_watches=False)
    app = changedetection_app({'datastore_path': folder, 'disable_checkver': True, 'batch_mode': True}, store)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    yield app
    store.stop_thread = True
    app.config.exit.set()
    from changedetectionio import worker_pool
    worker_pool.shutdown_workers()


@pytest.fixture
def client(application, monkeypatch):
    from changedetectionio import worker_pool
    monkeypatch.setattr(worker_pool, 'queue_item_async_safe', lambda *args, **kwargs: None)
    store = application.config['DATASTORE']
    for uuid in list(store.data['watching']):
        store.delete(uuid)
    application.config['LOGIN_DISABLED'] = False
    store.data['settings']['application']['password'] = False
    return application.test_client()


def csrf(client):
    response = client.get('/prices/')
    assert response.status_code == 200
    return {'X-CSRFToken': re.search(r'name="csrf-token" content="([^"]+)"', response.text).group(1)}


def add(client, **overrides):
    data = {'url': 'https://example.com/product', 'title': '耳机 · 黑色', 'alert_mode': 'target', 'target_price': '100'}
    data.update(overrides)
    response = client.post('/prices/api/products', json=data, headers=csrf(client))
    assert response.status_code == 201, response.text
    return response.json['id']


def test_empty_dashboard_and_assets(client):
    page = client.get('/prices/')
    assert page.status_code == 200
    assert '价格哨兵' in page.text
    assert 'csrf-token' in page.text
    assert client.get('/prices/api/products').json['stats']['total'] == 0
    assert client.get('/styles/price-sentinel.css').status_code == 200
    assert client.get('/js/price-sentinel.js').status_code == 200


def test_create_read_update_pause_delete(client, application):
    uuid = add(client)
    watch = application.config['DATASTORE'].data['watching'][uuid]
    watch.update({'restock': {'price': 89.9, 'last_price': 120, 'currency': 'CNY', 'in_stock': True}, 'last_error': False})
    listing = client.get('/prices/api/products').json
    assert listing['stats']['total'] == 1
    assert listing['stats']['target'] == 1
    assert listing['products'][0]['price'] == '89.9'
    assert listing['products'][0]['title'] == '耳机 · 黑色'
    headers = csrf(client)
    assert client.post(f'/prices/api/products/{uuid}/pause', json={'paused': True}, headers=headers).status_code == 200
    assert client.get('/prices/api/products').json['products'][0]['paused'] is True
    assert client.post(f'/prices/api/products/{uuid}/check', json={}, headers=headers).status_code == 409
    assert client.post(f'/prices/api/products/{uuid}/pause', json={'paused': False}, headers=headers).status_code == 200
    assert client.post(f'/prices/api/products/{uuid}/check', json={}, headers=headers).status_code == 202
    assert client.post(f'/prices/api/products/{uuid}/rule', json={'alert_mode': 'percentage', 'change_percent': 5}, headers=headers).status_code == 200
    updated = client.get('/prices/api/products').json['products'][0]
    assert updated['alert_mode'] == 'percentage'
    assert updated['target_price'] is None
    assert client.delete(f'/prices/api/products/{uuid}', headers=headers).status_code == 204
    assert client.get('/prices/api/products').json['stats']['total'] == 0


def test_csrf_is_enforced(client):
    response = client.post('/prices/api/products', json={'url': 'https://example.com'})
    assert response.status_code == 400
    assert client.get('/prices/api/products').json['stats']['total'] == 0


def test_bad_fields_do_not_create_watch(client):
    response = client.post('/prices/api/products', json={'url': 'https://example.com', 'interval_minutes': -1}, headers=csrf(client))
    assert response.status_code == 400
    assert client.get('/prices/api/products').json['stats']['total'] == 0


def test_non_price_watch_not_exposed_or_mutated(client, application):
    store = application.config['DATASTORE']
    uuid = store.add_watch('https://example.com/normal', extras={'processor': 'text_json_diff', 'paused': True})
    assert client.get('/prices/api/products').json['stats']['total'] == 0
    assert client.delete(f'/prices/api/products/{uuid}', headers=csrf(client)).status_code == 404
    assert uuid in store.data['watching']


def test_history_missing_prices_and_export(client, application):
    uuid = add(client)
    watch = application.config['DATASTORE'].data['watching'][uuid]
    now = int(time.time())
    watch.save_history_blob('In Stock: True - Price: 120', timestamp=now-20, snapshot_id=str(uuid4()))
    watch.save_history_blob('In Stock: False - Price: None', timestamp=now-10, snapshot_id=str(uuid4()))
    watch.save_history_blob('In Stock: True - Price: 89.9', timestamp=now, snapshot_id=str(uuid4()))
    result = client.get(f'/prices/api/products/{uuid}/history?days=7')
    assert result.status_code == 200
    assert [p['price'] for p in result.json['points']] == ['120', None, '89.9']
    assert client.get(f'/prices/api/products/{uuid}/history?days=bad').status_code == 400
    exported = client.get(f'/prices/api/products/{uuid}/history.csv?days=all')
    assert exported.status_code == 200
    assert 'text/csv' in exported.content_type
    assert '89.9' in exported.text
    assert '原始快照未记录币种' in exported.text


def test_password_protects_new_dashboard_and_api(client, application):
    settings = application.config['DATASTORE'].data['settings']['application']
    settings['password'] = 'test-password-present'
    try:
        assert client.get('/prices/').status_code == 302
        assert client.get('/prices/api/products').status_code == 302
    finally:
        settings['password'] = False
