"""中文价格监控工作台，复用 changedetection.io 的数据和工作队列。"""

import csv
import io
import time

from flask import Blueprint, Response, jsonify, render_template, request

from changedetectionio.auth_decorator import login_optionally_required
from .summary import create_payload, parse_snapshot, product_summary


def construct_blueprint(datastore, update_q):
    bp = Blueprint('price_sentinel', __name__)

    def get_watch(uuid):
        watch = datastore.data['watching'].get(uuid)
        return watch if watch is not None and watch.get('processor') == 'restock_diff' else None

    def config_for(watch):
        from changedetectionio.api import get_processor_api_fields
        fields = get_processor_api_fields(watch, datastore, context='get')
        return fields.get('processor_config_restock_diff') or {}

    def queue_watch(uuid):
        from changedetectionio import queuedWatchMetaData, worker_pool
        worker_pool.queue_item_async_safe(update_q, queuedWatchMetaData.PrioritizedItem(priority=1, item={'uuid': uuid}))

    @bp.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @bp.route('/')
    @login_optionally_required
    def index():
        return render_template('price-sentinel.html')

    @bp.route('/api/products')
    @login_optionally_required
    def products():
        result = [product_summary(watch, config_for(watch))
                  for watch in list(datastore.data['watching'].values())
                  if watch.get('processor') == 'restock_diff']
        result.sort(key=lambda row: (not row['target_reached'], row['title'] or ''))
        return jsonify(products=result, checked_at=int(time.time()),
                       stats={'total': len(result), 'active': sum(not p['paused'] for p in result),
                              'target': sum(p['target_reached'] for p in result),
                              'errors': sum(p['status'] == 'error' for p in result),
                              'down': sum(p['change_percent'] is not None and float(p['change_percent']) < 0
                                          and p['status'] not in ('error', 'waiting', 'paused') for p in result)})

    @bp.route('/api/products', methods=['POST'])
    @login_optionally_required
    def add_product():
        from changedetectionio.api.operations import ApiError, create_watch
        data = create_payload(request.get_json(silent=True))
        try:
            uuid = create_watch(datastore, data)
        except ApiError as error:
            return jsonify(error=error.message), error.status
        queue_watch(uuid)
        return jsonify(id=uuid), 201

    @bp.route('/api/products/<uuid>/pause', methods=['POST'])
    @login_optionally_required
    def pause(uuid):
        watch = get_watch(uuid)
        if watch is None:
            return jsonify(error='商品监控不存在'), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'paused'} or not isinstance(data['paused'], bool):
            raise ValueError('paused 应为布尔值')
        from changedetectionio.api.operations import update_watch
        update_watch(datastore, uuid, {'paused': data['paused']})
        if not data['paused']:
            queue_watch(uuid)
        return jsonify(ok=True)

    @bp.route('/api/products/<uuid>/rule', methods=['POST'])
    @login_optionally_required
    def rule(uuid):
        watch = get_watch(uuid)
        if watch is None:
            return jsonify(error='商品监控不存在'), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - {'alert_mode', 'target_price', 'change_percent'}:
            raise ValueError('提醒规则格式错误')
        payload = create_payload(dict(data, url=watch['url']))
        from changedetectionio.api.operations import ApiError, update_watch
        try:
            update_watch(datastore, uuid, {'processor_config_restock_diff': payload['processor_config_restock_diff']})
        except ApiError as error:
            return jsonify(error=error.message), error.status
        return jsonify(ok=True)

    @bp.route('/api/products/<uuid>/check', methods=['POST'])
    @login_optionally_required
    def recheck(uuid):
        watch = get_watch(uuid)
        if watch is None:
            return jsonify(error='商品监控不存在'), 404
        if watch.get('paused'):
            return jsonify(error='请先恢复监控'), 409
        queue_watch(uuid)
        return jsonify(queued=True), 202

    @bp.route('/api/products/<uuid>', methods=['DELETE'])
    @login_optionally_required
    def delete(uuid):
        if get_watch(uuid) is None:
            return jsonify(error='商品监控不存在'), 404
        datastore.delete(uuid)
        return '', 204

    def series_for(watch):
        days = request.args.get('days', '30')
        if days not in ('7', '30', '90', 'all'):
            raise ValueError('时间范围应为 7、30、90 或 all')
        after = time.time() - int(days) * 86400 if days != 'all' else 0
        history = watch.history or {}
        selected = []
        for key, path in history.items():
            try:
                timestamp = int(key)
            except (ValueError, TypeError):
                continue
            if timestamp >= after:
                selected.append((timestamp, path))
        selected.sort()
        truncated = len(selected) > 500
        result, unreadable = [], 0
        for timestamp, path in selected[-500:]:
            try:
                text = watch.get_history_snapshot(filepath=path)
                price, stock = parse_snapshot(text)
            except (OSError, ValueError, TypeError):
                unreadable += 1
                price, stock = None, None
            result.append({'timestamp': timestamp, 'price': price, 'in_stock': stock})
        return result, truncated, unreadable

    @bp.route('/api/products/<uuid>/history')
    @login_optionally_required
    def history(uuid):
        watch = get_watch(uuid)
        if watch is None:
            return jsonify(error='商品监控不存在'), 404
        points, truncated, unreadable = series_for(watch)
        return jsonify(points=points, truncated=truncated, unreadable=unreadable,
                       currency=(watch.get('restock') or {}).get('currency') or '',
                       scope='变化快照；最多最近500条。旧快照不含币种字段，请固定规格与币种。')

    @bp.route('/api/products/<uuid>/history.csv')
    @login_optionally_required
    def export(uuid):
        watch = get_watch(uuid)
        if watch is None:
            return jsonify(error='商品监控不存在'), 404
        points, _, _ = series_for(watch)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['时间戳', '价格', '库存状态', '币种口径'])
        for point in points:
            writer.writerow([point['timestamp'], point['price'] or '',
                             '有货' if point['in_stock'] is True else '缺货' if point['in_stock'] is False else '未知',
                             '原始快照未记录币种'])
        return Response('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="price-history-{uuid}.csv"'})

    return bp
