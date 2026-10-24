"""Groq-only routing, including migration of older provider preferences."""
import json


def settings(store):
    # Old panels and saved configurations cannot enable a removed provider.
    return {'mode': 'groq'}


def apply_pending_settings(store):
    with store._connection(write=True) as connection:
        current = connection.execute("SELECT value_json FROM metadata WHERE key='provider_settings'").fetchone()
        pending = connection.execute("SELECT value_json FROM metadata WHERE key='provider_pending'").fetchone()
        changed = current is None or json.loads(current['value_json']) != {'mode': 'groq'} or pending is not None
        connection.execute("INSERT INTO metadata(key,value_json) VALUES('provider_settings',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps({'mode': 'groq'}),))
        connection.execute("DELETE FROM metadata WHERE key='provider_pending'")
    if changed:
        store.record_event('INFO', 'GROQ_ONLY', 'Provider configuration migrated to Groq only; progress and cooldown preserved')
    return changed


def save_settings(store, mode, model='', num_ctx=None, *, defer=False):
    if mode != 'groq':
        raise ValueError('Доступен только Groq. Другие провайдеры удалены.')
    if not defer and store.get_control_state() != 'stopped':
        raise ValueError('Сначала остановите исполнителя.')
    apply_pending_settings(store)
    return settings(store)


def cancel_pending_settings(store):
    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM metadata WHERE key='provider_pending'")
