"""Local-only Ollama transport and persistent provider selection.

Routing generates proposals only. Submission and completion checks remain in
the existing handlers; local tokens are never charged to Groq's usage ledger.
"""
import json
import http.client
import queue
import re
import socket
import threading
import time
import urllib.request

OLLAMA_URL = 'http://127.0.0.1:11434'
LOCAL_TIMEOUT_SECONDS = 900
LOCAL_DEADLINE_SECONDS = 3600
DEFAULTS = {'mode': 'groq', 'ollama_model': '', 'num_ctx': 8192}


def settings(store):
    return {**DEFAULTS, **(store.get_meta('provider_settings') or {})}


def models():
    with urllib.request.urlopen(OLLAMA_URL + '/api/tags', timeout=3) as response:
        body = json.load(response)
    return sorted({m['name'] for m in body.get('models', [])
                   if isinstance(m.get('name'), str) and m.get('size', 0) > 0
                   and not re.search(r'[:\-]cloud$', m['name'])})


def save_settings(store, mode, model, num_ctx, *, defer=False):
    if not defer and store.get_control_state() != 'stopped':
        raise ValueError('Сначала остановите исполнителя и дождитесь состояния «Остановлен».')
    if mode not in {'groq', 'ollama', 'auto'}:
        raise ValueError('Неизвестный режим провайдера.')
    if not isinstance(model, str) or len(model) > 160 or not re.fullmatch(r'[\w.:/\-]*', model):
        raise ValueError('Недопустимое имя модели.')
    try:
        num_ctx = int(num_ctx)
    except (ValueError, TypeError):
        raise ValueError('Некорректный размер контекста.') from None
    if num_ctx not in {8192, 16384, 32768}:
        raise ValueError('Выберите контекст 8192, 16384 или 32768.')
    if mode != 'groq':
        try:
            available = models()
        except (OSError, ValueError):
            raise ValueError('Ollama недоступна на 127.0.0.1:11434. Запустите Ollama.') from None
        if model not in available:
            raise ValueError('Выберите установленную локальную модель Ollama.')
    value = {'mode': mode, 'ollama_model': model, 'num_ctx': num_ctx}
    with store._connection(write=True) as connection:
        state = connection.execute('SELECT state FROM control WHERE id=1').fetchone()['state']
        if state != 'stopped' and not defer:
            raise ValueError('Сначала остановите исполнителя.')
        queued = state != 'stopped'
        key = 'provider_pending' if queued else 'provider_settings'
        connection.execute('INSERT INTO metadata(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                           (key, json.dumps(value)))
        if not queued:
            connection.execute("DELETE FROM metadata WHERE key='provider_pending'")
    store.record_event('INFO', 'PROVIDER_SETTINGS_QUEUED' if queued else 'PROVIDER_SETTINGS',
                       f'Provider mode: {mode}; local model: {model or "none"}')
    return value


def apply_pending_settings(store):
    """Consume the latest selection atomically, only at a model request boundary."""
    with store._connection(write=True) as connection:
        row = connection.execute("SELECT value_json FROM metadata WHERE key='provider_pending'").fetchone()
        if row is None:
            return False
        connection.execute("INSERT INTO metadata(key,value_json) VALUES('provider_settings',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (row['value_json'],))
        connection.execute("DELETE FROM metadata WHERE key='provider_pending'")
    store.record_event('INFO', 'PROVIDER_SETTINGS_APPLIED', 'Selected provider settings applied before a new model request')
    return True


def cancel_pending_settings(store):
    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM metadata WHERE key='provider_pending'")


def validate_schema(value, schema):
    """Validate the small JSON-schema subset emitted by GroqClient._format."""
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'boolean': type(value) is bool, 'integer': type(value) is int,
             'string': isinstance(value, str)}
    if kind in valid and not valid[kind]:
        raise ValueError('Local output does not match the requested schema')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Local output contains an invalid option')
    if kind == 'object':
        props = schema.get('properties', {})
        if not set(schema.get('required', [])).issubset(value):
            raise ValueError('Local output is missing required fields')
        if schema.get('additionalProperties') is False and set(value) - set(props):
            raise ValueError('Local output contains unexpected fields')
        for key, child in props.items():
            if key in value:
                validate_schema(value[key], child)
    elif kind == 'array':
        for item in value:
            validate_schema(item, schema.get('items', {}))


def ollama_transport(config, payload, checkpoint, progress=None):
    from .groq_client import GroqError, TruncatedResponse
    checkpoint()
    model = config['ollama_model']
    if not model or model not in models():
        raise GroqError('Configured local Ollama model is unavailable')
    schema = payload.get('response_format', {}).get('json_schema', {}).get('schema')
    output_budget = payload.get('max_completion_tokens', 2048)
    # UTF-8 bytes provide a conservative token bound. Refuse an oversized input
    # instead of letting Ollama silently discard task instructions or feedback.
    input_bound = sum(len(m['content'].encode('utf-8')) + 64 for m in payload['messages'])
    if input_bound + output_budget + 256 > config['num_ctx']:
        raise GroqError('Local context budget too small; increase Ollama context or use Groq')
    request = json.dumps({'model': model, 'messages': payload['messages'], 'stream': True,
            'format': schema or 'json', 'keep_alive': '5m',
            'options': {'temperature': 0, 'num_predict': output_budget, 'num_ctx': config['num_ctx']}}).encode()
    result_queue = queue.Queue(maxsize=1)
    cancelled = threading.Event()
    connection = http.client.HTTPConnection('127.0.0.1', 11434, timeout=3)
    connected_socket = [None]
    stats_lock = threading.Lock()
    stats = {'chunks': 0, 'response_characters': 0, 'thinking_characters': 0}

    def cancel_connection():
        cancelled.set()
        # close() alone does not interrupt a read held by HTTPResponse.makefile.
        sock = connected_socket[0] or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    def generate():
        try:
            connection.connect()
            connected_socket[0] = connection.sock
            if cancelled.is_set():
                return
            connection.sock.settimeout(LOCAL_TIMEOUT_SECONDS)
            connection.request('POST', '/api/chat', body=request, headers={'Content-Type': 'application/json'})
            with connection.getresponse() as response:
                if response.status != 200:
                    raise ValueError('local HTTP error')
                parts, total_bytes = [], 0
                while not cancelled.is_set():
                    line = response.readline(2_000_001)
                    if not line:
                        raise ValueError('stream ended without completion')
                    total_bytes += len(line)
                    if len(line) > 2_000_000 or total_bytes > 8_000_000:
                        raise ValueError('stream too large')
                    body = json.loads(line)
                    if 'error' in body:
                        raise ValueError('local generation error')
                    message = body.get('message') or {}
                    content, thinking = message.get('content', ''), message.get('thinking', '')
                    if not isinstance(content, str) or not isinstance(thinking, str):
                        raise ValueError('invalid stream content')
                    parts.append(content)
                    with stats_lock:
                        stats['chunks'] += 1
                        stats['response_characters'] += len(content)
                        stats['thinking_characters'] += len(thinking)
                        stats['last_chunk_at'] = time.time()
                        for field in ('eval_count', 'prompt_eval_count'):
                            if type(body.get(field)) is int:
                                stats[field] = body[field]
                    if body.get('done') is True:
                        body['message'] = {'content': ''.join(parts)}
                        result_queue.put((True, body))
                        return
        except Exception:
            # Do not expose provider response bodies, prompts or answers.
            result_queue.put((False, None))
        finally:
            connection.close()

    checkpoint()
    threading.Thread(target=generate, daemon=True, name='ollama-generation').start()
    deadline = time.monotonic() + LOCAL_DEADLINE_SECONDS
    reported_at = 0
    try:
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise GroqError('Ollama generation deadline exceeded; no result applied')
            if progress and time.monotonic() - reported_at >= 1:
                with stats_lock:
                    snapshot = dict(stats)
                progress(snapshot)
                reported_at = time.monotonic()
            try:
                ok, body = result_queue.get(timeout=0.25)
                break
            except queue.Empty:
                pass
        checkpoint()  # A stopped request must not be saved or applied.
        if progress:
            with stats_lock:
                snapshot = dict(stats)
            progress(snapshot)
    finally:
        cancel_connection()
    if not ok:
        raise GroqError('Ollama request failed or timed out; no result applied')
    if body.get('done_reason') == 'length':
        raise TruncatedResponse('Ollama response exceeded the output budget')
    if body.get('done') is not True or body.get('done_reason') != 'stop':
        raise GroqError('Ollama returned an incomplete response')
    try:
        answer = json.loads(body['message']['content'])
        if schema:
            validate_schema(answer, schema)
    except (KeyError, TypeError, ValueError):
        raise GroqError('Ollama returned invalid JSON/schema; no result applied') from None
    return answer
