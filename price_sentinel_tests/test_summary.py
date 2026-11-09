from decimal import Decimal

import pytest

from changedetectionio.blueprint.price_sentinel.summary import (
    amount, change_percent, create_payload, parse_snapshot, product_summary,
)


@pytest.mark.parametrize('value', [None, True, False, 0, -1, 'NaN', 'Infinity', '-Infinity', '1e99999', '1e-99999', '免费', ''])
def test_invalid_prices_never_become_zero(value):
    assert amount(value) is None


def test_decimal_changes():
    assert amount('1999.90') == Decimal('1999.90')
    assert change_percent('899', '1099') == '-18.20'
    assert change_percent('0.30', '0.10') == '200.00'
    assert change_percent('100', 0) is None


def test_create_target_preserves_interval_and_chooses_one_rule():
    result = create_payload({'url': 'https://example.com/item?sku=black', 'title': '耳机 · 黑色',
                             'interval_minutes': 30, 'alert_mode': 'target', 'target_price': '1999.90'})
    assert result['processor'] == 'restock_diff'
    assert result['time_between_check']['minutes'] == 30
    config = result['processor_config_restock_diff']
    assert config['price_change_min'] == 1999.9
    assert config['price_change_threshold_percent'] is None
    assert config['in_stock_processing'] == 'in_stock_only'


@pytest.mark.parametrize('data', [None, [], {'url': 'file:///etc/passwd'}, {'url': 'javascript:alert(1)'},
    {'url': 'https://user:pass@example.com/item'}, {'url': 'https://example.com', 'interval_minutes': True},
    {'url': 'https://example.com', 'interval_minutes': 0}, {'url': 'https://example.com', 'interval_minutes': 1.5},
    {'url': 'https://example.com', 'alert_mode': 'percentage', 'change_percent': 101},
    {'url': 'https://example.com', 'alert_mode': 'target', 'target_price': 'NaN'},
    {'url': 'https://example.com', 'notification_urls': ['secret']},
    {'url': 'https://example.com', 'fetch_backend': 'arbitrary'}])
def test_bad_inputs_rejected(data):
    with pytest.raises(ValueError):
        create_payload(data)


def test_summary_does_not_treat_failure_or_missing_stock_as_free():
    watch = {'uuid': 'test', 'url': 'https://example.com', 'restock': {'price': 99, 'last_price': 120, 'in_stock': True}, 'last_error': 'HTTP 403'}
    result = product_summary(watch, {'price_change_min': 100})
    assert result['status'] == 'error'
    assert not result['target_reached']
    watch['last_error'] = ''
    watch['restock']['in_stock'] = False
    assert product_summary(watch, {'price_change_min': 100})['status'] == 'out_of_stock'
    watch['restock']['price'] = None
    assert product_summary(watch, {})['price'] is None


def test_target_comparison_is_strict_and_paused_is_excluded():
    watch = {'uuid': 'test', 'restock': {'price': 100, 'in_stock': True}}
    assert not product_summary(watch, {'price_change_min': 100})['target_reached']
    watch['restock']['price'] = 99
    assert product_summary(watch, {'price_change_min': 100})['target_reached']
    watch['paused'] = True
    assert not product_summary(watch, {'price_change_min': 100})['target_reached']


@pytest.mark.parametrize('snapshot,expected', [
    ('In Stock: True - Price: 1299.95', ('1299.95', True)),
    ('In Stock: False - Price: None', (None, False)),
    ('In Stock: True - Price: NaN', (None, True)),
    ('In Stock: True - Price: -199', (None, True)),
    ('<h1>验证码</h1>', (None, None)),
    (None, (None, None)),
])
def test_snapshots(snapshot, expected):
    assert parse_snapshot(snapshot) == expected
