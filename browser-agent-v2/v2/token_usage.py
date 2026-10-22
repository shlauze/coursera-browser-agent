"""Observed Groq usage only; no prompts, answers, credentials or estimated history."""
from datetime import datetime, timezone, timedelta
from html import escape

LOCAL = timezone(timedelta(hours=5))


def record_usage(store, body, status, model, course_id=None, action_id=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    def count(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None
    with store._connection(write=True) as c:
        c.execute("""INSERT INTO groq_usage
            (observed_at,day,course_id,action_id,model,http_status,prompt_tokens,completion_tokens,total_tokens)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now.astimezone(timezone.utc).isoformat(),
            now.astimezone(LOCAL).date().isoformat(), course_id, action_id, str(model), status,
            count("prompt_tokens"), count("completion_tokens"), count("total_tokens")))


def usage_summary(store):
    with store._connection() as c:
        totals = dict(c.execute("""SELECT count(*) requests, count(*)-count(total_tokens) unknown_requests,
            sum(prompt_tokens) prompt_tokens, sum(completion_tokens) completion_tokens,
            sum(total_tokens) total_tokens, min(observed_at) since FROM groq_usage""").fetchone())
        rows = [dict(r) for r in c.execute("""SELECT u.day,u.course_id,
            coalesce(c.title,'Без курса') course, count(*) requests,
            count(*)-count(u.total_tokens) unknown_requests,
            sum(u.prompt_tokens) prompt_tokens, sum(u.completion_tokens) completion_tokens,
            sum(u.total_tokens) total_tokens FROM groq_usage u
            LEFT JOIN courses c ON c.course_id=u.course_id
            GROUP BY u.day,u.course_id ORDER BY u.day DESC, course""")]
    return {"timezone": "UTC+5", "totals": totals, "by_day_course": rows}


def usage_html(summary):
    def number(value):
        return "—" if value is None else f"{value:,}".replace(",", " ")
    totals = summary["totals"]
    rows = "".join("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in (
        row["day"], row["course"], row["requests"], number(row["prompt_tokens"]),
        number(row["completion_tokens"]), number(row["total_tokens"]), row["unknown_requests"]))
        + "</tr>" for row in summary["by_day_course"])
    return ('<section class="panel"><h2>Расход токенов Groq</h2>'
        '<p>Всего учтено: ' + number(totals["total_tokens"]) + ' токенов. Запросов: '
        + str(totals["requests"]) + '. Без данных о суммарном расходе: '
        + str(totals["unknown_requests"]) + '.</p>'
        '<p class="sub">Дни: UTC+5. Учёт с версии 0.2.40; прежний расход неизвестен. '
        'Показана только usage из ответов API, включая неудачный разбор ответа. '
        'Кэш не создаёт запросов. Это расход, а не остаток квоты. Прочерк означает отсутствие данных.</p>'
        '<div style="overflow-x:auto"><table class="events"><thead><tr><th>День</th><th>Курс</th>'
        '<th>Запросы</th><th>Вход</th><th>Выход</th><th>Всего</th><th>Без usage total</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table></div></section>')
