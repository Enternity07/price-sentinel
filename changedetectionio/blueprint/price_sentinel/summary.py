"""价格工作台的数值、输入校验与快照转换；不改变上游检测规则。"""

from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and Decimal('0.000001') <= number <= Decimal('1000000000000') else None


def decimal_text(value):
    number = amount(value)
    return format(number, 'f') if number is not None else None


def change_percent(price, previous):
    current, baseline = amount(price), amount(previous)
    if current is None or baseline is None:
        return None
    return str(((current - baseline) / baseline * 100).quantize(Decimal('0.01')))


def create_payload(data):
    if not isinstance(data, dict):
        raise ValueError('请提交商品信息对象')
    allowed = {'url', 'title', 'interval_minutes', 'alert_mode', 'target_price', 'change_percent', 'fetch_backend'}
    if set(data) - allowed:
        raise ValueError('包含未支持的字段')
    url, title = data.get('url'), data.get('title', '')
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('请输入有效商品链接')
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        raise ValueError('商品链接格式错误') from None
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('商品链接仅支持不含账号密码的 HTTP 或 HTTPS 地址')
    if not isinstance(title, str) or len(title.strip()) > 200:
        raise ValueError('商品名称请控制在 200 个字符以内')
    interval = data.get('interval_minutes', 15)
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 10080:
        raise ValueError('检查间隔应为 1 至 10080 的整数分钟')
    mode = data.get('alert_mode', 'all')
    if mode not in ('all', 'target', 'percentage'):
        raise ValueError('提醒规则不受支持')
    config = {'follow_price_changes': True, 'in_stock_processing': 'in_stock_only',
              'price_change_min': None, 'price_change_max': None, 'price_change_threshold_percent': None}
    if mode == 'target':
        target = amount(data.get('target_price'))
        if target is None:
            raise ValueError('目标价应为有效正数')
        config['price_change_min'] = float(target)
    elif mode == 'percentage':
        threshold = amount(data.get('change_percent'))
        if threshold is None or threshold > 100:
            raise ValueError('波动阈值应大于 0 且不超过 100%')
        config['price_change_threshold_percent'] = float(threshold)
    backend = data.get('fetch_backend', 'html_requests')
    if backend not in ('html_requests', 'html_webdriver'):
        raise ValueError('采集方式不受支持')
    return {'url': url.strip(), 'title': title.strip() or parsed.hostname,
            'processor': 'restock_diff', 'fetch_backend': backend,
            'time_between_check_use_default': False,
            'time_between_check': {'weeks': 0, 'days': 0, 'hours': 0, 'minutes': interval, 'seconds': 0},
            'processor_config_restock_diff': config}


def product_summary(watch, config):
    state = watch.get('restock') or {}
    price, previous, target = (amount(state.get('price')), amount(state.get('last_price')),
                                amount(config.get('price_change_min')))
    failed, paused = bool(watch.get('last_error')), bool(watch.get('paused'))
    stock = state.get('in_stock') if isinstance(state.get('in_stock'), bool) else None
    percent = change_percent(price, previous)
    reached = bool(price is not None and target is not None and price < target
                   and stock is not False and not failed and not paused)
    status = ('paused' if paused else 'error' if failed else 'waiting' if price is None
              else 'out_of_stock' if stock is False else 'target' if reached else 'tracking')
    return {'id': watch.get('uuid'), 'title': watch.get('title') or watch.get('page_title') or watch.get('url'),
            'url': watch.get('url'), 'currency': state.get('currency') or '',
            'price': decimal_text(price), 'previous_price': decimal_text(previous),
            'target_price': decimal_text(target), 'change_percent': percent,
            'target_reached': reached, 'in_stock': stock, 'paused': paused, 'status': status,
            'last_checked': watch.get('last_checked') or 0,
            'error': str(watch.get('last_error') or '')[:400],
            'alert_mode': 'target' if target is not None else 'percentage' if config.get('price_change_threshold_percent') else 'all',
            'threshold_percent': config.get('price_change_threshold_percent'),
            'fetch_backend': watch.get('fetch_backend', 'system')}


def parse_snapshot(text):
    if not isinstance(text, str):
        return None, None
    price = re.search(r'\bPrice:\s*([^\s]+)', text, re.I)
    stock = re.search(r'\bIn Stock:\s*(True|False)\b', text, re.I)
    return (decimal_text(price.group(1)) if price else None,
            stock.group(1).lower() == 'true' if stock else None)
