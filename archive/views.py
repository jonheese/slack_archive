"""HTTP validation and template context; query and formatting code live elsewhere."""
from datetime import datetime
from time import perf_counter
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from flask import Blueprint, abort, current_app, g, render_template, request
from mysql.connector import Error
from werkzeug.datastructures import CombinedMultiDict
from . import messages, queries

pages = Blueprint('pages', __name__)


@pages.before_request
def start_timer():
    g.request_start = perf_counter()


@pages.after_request
def timing(response):
    elapsed = perf_counter() - g.request_start
    query_ms = g.get('query_seconds', 0) * 1000
    response.headers['Server-Timing'] = f'db;dur={query_ms:.1f}, app;dur={elapsed * 1000:.1f}'
    current_app.logger.info('request path=%s status=%s queries=%s db_ms=%.1f total_ms=%.1f',
                            request.path, response.status_code, g.get('query_count', 0), query_ms, elapsed * 1000)
    return response


def integer(value, name, minimum=None, maximum=None):
    try:
        result = int(value)
    except (ValueError, TypeError):
        abort(400, description=f'Invalid {name}.')
    if (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        abort(400, description=f'Invalid {name}.')
    return result


@pages.route('/favicon.ico')
def favicon():
    return current_app.send_static_file('favicon.ico')


@pages.route('/')
def search():
    options = queries.filters(request.args)
    page = integer(request.args.get('page', 1), 'page', 1, 2147483647)
    per_page = integer(request.args.get('rpp', 20), 'results per page', 1, 200)
    try:
        rows, count = queries.search(options, page, per_page)
    except ValueError:
        abort(400, description='Invalid date; use YYYY-MM-DD.')
    except Error:
        current_app.logger.exception('Search query failed')
        return render_template('results.j2', **options, messages=[], errors=['Database query failed. Please try again.']), 503
    if count is None:
        return render_template('results.j2', **options, messages=[])
    try:
        records = messages.package(rows, options['q'])
    except Error:
        current_app.logger.exception('Message enrichment failed')
        return render_template('results.j2', **options, messages=[], errors=['Database query failed. Please try again.']), 503
    offset = (page - 1) * per_page
    params = dict(options, rpp=per_page)
    return render_template('results.j2', **options, messages=records,
                           total_count=count, page=page,
                           message_range=f'{offset + 1 if rows else 0} - {min(offset + len(rows), count) if rows else 0}',
                           show_prev=page > 1, show_next=offset + per_page < count,
                           prev_page_url='/?' + urlencode(dict(params, page=page - 1)),
                           next_page_url='/?' + urlencode(dict(params, page=page + 1)))


@pages.route('/context/<message_id>/', methods=['GET', 'POST'])
@pages.route('/context/<message_id>/<q>', methods=['GET', 'POST'])
@pages.route('/context/<message_id>/<q>/', methods=['GET', 'POST'])
def context(message_id, q=None):
    message_id = integer(message_id, 'message ID', 1, 18446744073709551615)
    args = CombinedMultiDict([request.form, request.args])
    options = queries.filters(args)
    if q is not None:
        options['q'] = q
    offset = integer(args.get('offset', 0), 'offset', -2147483647, 2147483647)
    base = f'/context/{message_id}/'
    common = dict(options, offset=offset, message_id=message_id, urlpath=base,
                  back_url='/?' + urlencode(options))
    try:
        anchor, rows = queries.context(message_id, offset)
        if anchor is None:
            abort(404, description='Message not found.')
        records = messages.package(rows, options['q'], details=True)
    except Error:
        current_app.logger.exception('Context query failed')
        return render_template('context.j2', **common, messages=[], errors=['Database query failed. Please try again.']), 503
    previous_user, previous_date = None, None
    for record in records:
        record['display_user'] = record['username'] != previous_user
        record['display_date'] = record['date'] != previous_date
        # A new date must remain visible even if the same author continues posting.
        record['display_user'] = record['display_user'] or record['display_date']
        previous_user, previous_date = record['username'], record['date']
    stamp = datetime.fromtimestamp(anchor['timestamp'], ZoneInfo(current_app.config['DISPLAY_TIMEZONE']))
    summary = f"{anchor['full_name']} ({stamp.strftime('%I:%M %p %Y-%m-%d (%a)')}): {anchor['text'] or ''}"
    navigation = {name: base + '?' + urlencode(dict(options, offset=offset + delta)) for name, delta in
                  (('prev20_url', -20), ('prev_url', -1), ('next_url', 1), ('next20_url', 20))}
    return render_template('context.j2', **common, **navigation, messages=records, summary=summary)
