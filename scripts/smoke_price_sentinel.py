"""对本机已运行的哨兵做真实采集冒烟测试，商品页面由临时 HTTP 服务提供。"""

import argparse
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:5080')
    args = parser.parse_args()
    if not re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):\d+', args.base_url):
        raise SystemExit('仅对明确指定的本机测试实例执行')
    state = {'price': '120.00', 'status': 200}

    class Shop(BaseHTTPRequestHandler):
        def do_GET(self):
            if state['status'] != 200:
                self.send_response(state['status'])
                self.end_headers()
                self.wfile.write(b'access failed')
                return
            product = {'@context': 'https://schema.org', '@type': 'Product', 'name': '冒烟测试商品',
                       'sku': 'smoke-black', 'offers': {'@type': 'Offer', 'price': state['price'],
                       'priceCurrency': 'CNY', 'availability': 'https://schema.org/InStock'}}
            html = ('<!doctype html><html><head><title>冒烟测试商品</title><script type="application/ld+json">'
                    + json.dumps(product, ensure_ascii=False) + '</script></head><body>固定黑色规格</body></html>')
            data = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Shop)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = requests.Session()
    session.trust_env = False
    base = args.base_url + '/prices'
    page = session.get(base + '/', timeout=10)
    page.raise_for_status()
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    session.headers['X-CSRFToken'] = token
    uuid = None

    def post(path, data):
        response = session.post(base + '/api/products' + path, json=data, timeout=10)
        response.raise_for_status()
        return response.json()

    def wait_for(predicate, timeout=60):
        until = time.monotonic() + timeout
        last = None
        while time.monotonic() < until:
            response = session.get(base + '/api/products', timeout=10)
            response.raise_for_status()
            last = next((p for p in response.json()['products'] if p['id'] == uuid), None)
            if last and predicate(last):
                return last
            time.sleep(.5)
        raise AssertionError('采集超时：' + json.dumps(last, ensure_ascii=False))

    try:
        created = post('', {'url': f'http://127.0.0.1:{server.server_port}/product?sku=smoke-black',
                            'title': '冒烟测试商品 · 固定黑色', 'alert_mode': 'target', 'target_price': '100',
                            'interval_minutes': 60})
        uuid = created['id']
        first = wait_for(lambda p: p['price'] is not None and Decimal(p['price']) == Decimal('120'))
        assert not first['target_reached']
        state['price'] = '89.90'
        post(f'/{uuid}/check', {})
        low = wait_for(lambda p: p['price'] is not None and Decimal(p['price']) == Decimal('89.90'))
        assert low['target_reached']
        history = session.get(base + f'/api/products/{uuid}/history?days=all', timeout=10).json()
        assert len(history['points']) >= 2
        count = len(history['points'])
        checked_at = low['last_checked']
        time.sleep(1.1)
        post(f'/{uuid}/check', {})
        wait_for(lambda p: p['last_checked'] > checked_at)
        unchanged = session.get(base + f'/api/products/{uuid}/history?days=all', timeout=10).json()
        assert len(unchanged['points']) == count
        state['status'] = 403
        post(f'/{uuid}/check', {})
        error = wait_for(lambda p: p['status'] == 'error')
        assert Decimal(error['price']) == Decimal('89.90')
        assert not error['target_reached']
        state.update(price='119.90', status=200)
        post(f'/{uuid}/check', {})
        recovered = wait_for(lambda p: p['status'] != 'error' and p['price'] is not None and Decimal(p['price']) == Decimal('119.90'))
        assert not recovered['target_reached']
        print(json.dumps({'result': 'PASS', 'initial_price': first['price'], 'lower_price': low['price'],
                          'target_triggered': low['target_reached'], 'unchanged_did_not_add_history': True,
                          'http_403_marked_error': True, 'error_did_not_become_zero': True,
                          'recovered_price': recovered['price']}, ensure_ascii=False))
    finally:
        if uuid:
            response = session.delete(base + f'/api/products/{uuid}', timeout=10)
            response.raise_for_status()
        server.shutdown()


if __name__ == '__main__':
    main()
