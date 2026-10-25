"""Passive, durable quota observations. Never probes or rotates credentials."""
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone, timedelta
from html import escape


def key_id(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]


def register_key(store, key, label=None):
    identity = key_id(key)
    if label is not None and (not re.fullmatch(r'[\w .-]{1,40}', label) or 'gsk_' in label.lower()):
        raise ValueError('Use a short key name, never the credential itself')
    with store._connection(write=True) as connection:
        row = connection.execute("SELECT value_json FROM metadata WHERE key='groq_key_inventory'").fetchone()
        inventory = json.loads(row['value_json']) if row else {}
        inventory[identity] = {'label': label or inventory.get(identity, {}).get('label', 'Groq ' + identity[:6])}
        connection.execute("INSERT INTO metadata(key,value_json) VALUES('groq_key_inventory',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(inventory),))
    return identity


def state_key(identity, model):
    return 'groq_key_state:' + identity + ':' + model


def retry_at(store, identity, model):
    return float((store.get_meta(state_key(identity, model)) or {}).get('retry_at', 0))


def observe(store, identity, model, status, headers, body, *, now, delay):
    """Only numeric quota fields enter SQLite/UI; no keys or response content."""
    name = state_key(identity, model)
    previous = store.get_meta(name) or {}
    limits = {}
    for dimension in ('requests', 'tokens'):
        for kind in ('limit', 'remaining', 'reset'):
            field = 'x-ratelimit-' + kind + '-' + dimension
            value = str(headers.get(field, ''))
            pattern = r'(?:\d+(?:\.\d+)?(?:ms|s|m|h|d))+' if kind == 'reset' else r'\d+'
            if len(value) <= 80 and re.fullmatch(pattern, value):
                limits[field] = value
    error = body.get('error', {}) if isinstance(body, dict) else {}
    message = str(error.get('message', '')) if isinstance(error, dict) else ''
    metric = re.search(r'\b(TPD|TPM|RPD|RPM|ITPM|OTPM)\b', message, re.I)
    rate = {'metric': metric[1].upper() if metric else 'unknown'}
    for field in ('limit', 'used', 'requested'):
        match = re.search(r'\b' + field + r'\s*[:=]?\s*(\d[\d,]*(?:\.\d+)?)', message, re.I)
        if match:
            rate[field] = match[1].replace(',', '')[:40]
    waiting = status in {0, 408, 429, 500, 502, 503, 504}
    row = {**previous, 'observed_at': now, 'http_status': status,
           'retry_at': max(previous.get('retry_at', 0), now + delay) if waiting else 0}
    if limits:
        row.update(headers=limits, headers_observed_at=now)
    if status == 429:
        row.update(rate_limit=rate, rate_limit_observed_at=now)
    store.set_meta(name, row)


def key_status(store, *, model=None, now=None):
    now = time.time() if now is None else now
    model = model or (store.get_meta('provider_active') or {}).get('model', 'openai/gpt-oss-120b')
    active = store.get_meta('groq_active_key_id')
    rows = []
    for identity, info in (store.get_meta('groq_key_inventory') or {}).items():
        row = store.get_meta(state_key(identity, model)) or {}
        remaining = max(0, math.ceil(row.get('retry_at', 0) - now))
        status = ('waiting' if remaining else 'unknown' if not row else
                  'access_error' if row.get('http_status') in (401, 403) else
                  'retry_due' if row.get('http_status') in (0, 408, 429, 500, 502, 503, 504) else 'observed')
        rows.append({'id': identity, 'label': info['label'], 'active': identity == active,
                     'model': model, **row, 'remaining_seconds': remaining, 'state': status})
    return rows


def limits_html(rows):
    def when(value):
        return datetime.fromtimestamp(value, timezone(timedelta(hours=5))).strftime('%d.%m %H:%M:%S UTC+5') if value else 'Нет данных'
    states = {'waiting': 'Ожидание', 'unknown': 'Нет данных API', 'access_error': 'Ошибка доступа',
              'retry_due': 'Срок ожидания истёк; требуется ответ API', 'observed': 'Ответ API получен'}
    rendered = []
    for row in rows:
        headers = row.get('headers', {})
        quota = []
        for dimension, label in [('requests', 'Запросы за сутки (RPD)'), ('tokens', 'Токены в минуту (TPM)')]:
            remaining = headers.get('x-ratelimit-remaining-' + dimension, '?')
            limit = headers.get('x-ratelimit-limit-' + dimension, '?')
            reset = headers.get('x-ratelimit-reset-' + dimension, '?')
            quota.append(f'{label}: {remaining} / {limit}; сброс по ответу API: {reset}')
        rate = row.get('rate_limit') or {}
        if rate:
            quota.append(f"Последнее ограничение {rate.get('metric')}: использовано {rate.get('used', '?')} / {rate.get('limit', '?')}; запрос {rate.get('requested', '?')} ({when(row.get('rate_limit_observed_at'))})")
        values = [row['label'] + (' — активный' if row['active'] else ''), states[row['state']],
                  '\n'.join(quota) if headers or rate else 'Неизвестно',
                  when(row.get('headers_observed_at')), when(row.get('retry_at')) if row.get('retry_at') else '—']
        rendered.append('<tr>' + ''.join('<td>' + escape(str(v)).replace('\n', '<br>') + '</td>' for v in values) + '</tr>')
    model = escape(rows[0]['model']) if rows else 'текущей модели'
    return ('<section class="panel"><h2>Лимиты ключей Groq</h2>'
            f'<p class="sub">Данные из ответов API для {model}. Квоты организации могут быть общими для нескольких ключей. '
            'Неактивные ключи не опрашиваются; остатки без нового ответа не пересчитываются. '
            'Истечение Retry-After разрешает следующую попытку и не означает полного восстановления квоты. '
            'Активный ключ продолжает автоматически, пока исполнитель работает. Ручная пауза и стоп сохраняются.</p>'
            '<div style="overflow-x:auto"><table class="events"><thead><tr><th>Ключ</th><th>Состояние</th><th>Последние лимиты</th>'
            '<th>Лимиты наблюдались</th><th>Следующая попытка не раньше</th></tr></thead><tbody>'
            + (''.join(rendered) or '<tr><td colspan="5">Ключи ещё не зарегистрированы</td></tr>') + '</tbody></table></div></section>')
