"""SQL construction. Values are always passed separately to the driver."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import current_app
from . import db

JOINS = ''' FROM tbl_messages m
JOIN tbl_users u ON u.id = m.user_id
JOIN tbl_channels c ON c.id = m.channel_id
JOIN tbl_teams t ON t.id = m.team_id'''
COLUMNS = '''SELECT m.team_id, m.channel_id, t.team_name, u.username,
m.timestamp, m.text, m.id, c.channel_name, c.slack_channel_id,
u.slack_user_id, u.full_name, u.avatar_url, m.archive_url, m.is_image'''


def truth(value):
    return str(value).lower() in ('true', '1', 'on', 'yes')


def filters(args):
    result = {key: args.get(key, '') for key in
              ('q', 'username', 'team_name', 'channel_name', 'start_date', 'end_date')}
    result['case_sensitive'] = truth(args.get('case_sensitive', False))
    # Honor explicit old bookmarks; absence now selects full-text search.
    result['exact_text'] = truth(args.get('exact_text', False))
    if 'exact_text' not in args and 'sql_match' in args:
        result['exact_text'] = not truth(args['sql_match'])
    result['exact_text'] = result['exact_text'] or result['case_sensitive']
    return result


def literal_pattern(value):
    # An explicit escape character works independently of NO_BACKSLASH_ESCAPES.
    return '%' + value.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


def fulltext_query(value):
    # Treat user input as words, never as a MySQL boolean expression.
    words = re.findall(r"\w+(?:'\w+)*", value, flags=re.UNICODE)
    return ' '.join('+' + word for word in dict.fromkeys(words))


def where_clause(options):
    clauses, params = [], []
    for key, columns in (
        ('username', ('u.username', 'u.slack_user_id', 'u.full_name')),
        ('team_name', ('t.team_name',)),
        ('channel_name', ('c.channel_name', 'c.slack_channel_id')),
    ):
        value = options[key]
        if not value:
            continue
        exclude = value.startswith('-')
        if exclude:
            value = value[1:]
        operator, joiner = ('NOT LIKE', ' AND ') if exclude else ('LIKE', ' OR ')
        clauses.append('(' + joiner.join(f"{column} {operator} %s ESCAPE '!'" for column in columns) + ')')
        params.extend([literal_pattern(value)] * len(columns))
    zone = ZoneInfo(current_app.config['DISPLAY_TIMEZONE'])
    for key, operator in (('start_date', '>'), ('end_date', '<')):
        if options[key]:
            # Preserve the old exclusive midnight boundaries, explicitly in display timezone.
            stamp = datetime.strptime(options[key], '%Y-%m-%d').replace(tzinfo=zone).timestamp()
            clauses.append(f'm.timestamp {operator} %s')
            params.append(int(stamp))
    if options['q']:
        if options['exact_text']:
            column = 'CAST(m.text AS BINARY)' if options['case_sensitive'] else 'm.text'
            clauses.append(f"{column} LIKE %s ESCAPE '!'")
            params.append(literal_pattern(options['q']))
        else:
            term = fulltext_query(options['q'])
            if term:
                clauses.append('MATCH(m.text) AGAINST (%s IN BOOLEAN MODE)')
                params.append(term)
            else:
                clauses.append('0 = 1')
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), tuple(params)


def search(options, page, per_page):
    where, params = where_clause(options)
    if not where:
        return [], None
    count = db.select('SELECT COUNT(*) AS total' + JOINS + where, params)[0]['total']
    if not count:
        return [], 0
    rows = db.select(COLUMNS + JOINS + where + ' ORDER BY m.timestamp, m.id LIMIT %s OFFSET %s',
                     params + (per_page, (page - 1) * per_page))
    return rows, count


def context(message_id, offset):
    anchors = db.select(COLUMNS + JOINS + ' WHERE m.id = %s', (message_id,))
    if not anchors:
        return None, []
    anchor = anchors[0]
    sql_offset = max(abs(offset) - 10, 0)
    rows = []
    for before, limit in ((True, min(10 - offset, 20)), (False, min(10 + offset, 20))):
        if limit <= 0:
            continue
        strict, inclusive, order = ('<', '<=', 'DESC') if before else ('>', '>', 'ASC')
        where = (f' WHERE m.channel_id = %s AND (m.timestamp {strict} %s OR '
                 f'(m.timestamp = %s AND m.id {inclusive} %s))')
        part = db.select(COLUMNS + JOINS + where + f' ORDER BY m.timestamp {order}, m.id {order} LIMIT %s OFFSET %s',
                         (anchor['channel_id'], anchor['timestamp'], anchor['timestamp'], anchor['id'], limit, sql_offset))
        rows.extend(reversed(part) if before else part)
    return anchor, rows
