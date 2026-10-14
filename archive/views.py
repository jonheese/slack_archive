"""Validate HTTP inputs, call application logic, and render existing templates."""

from datetime import datetime
from time import perf_counter
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, g, render_template, request
from mysql.connector import Error
from werkzeug.datastructures import CombinedMultiDict

from . import messages, queries

pages = Blueprint("pages", __name__)
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
MAX_OFFSET = 2147483647
MAX_MESSAGE_ID = 18446744073709551615
DATABASE_ERROR = "Database query failed. Please try again."
CONTEXT_STEPS = (
    ("prev20_url", -queries.CONTEXT_WINDOW_SIZE),
    ("prev_url", -1),
    ("next_url", 1),
    ("next20_url", queries.CONTEXT_WINDOW_SIZE),
)


@pages.before_request
def start_timer():
    g.request_start = perf_counter()


@pages.after_request
def timing(response):
    total_ms = (perf_counter() - g.request_start) * 1000
    query_ms = g.get("query_seconds", 0) * 1000
    response.headers["Server-Timing"] = f"db;dur={query_ms:.1f}, app;dur={total_ms:.1f}"
    current_app.logger.info(
        "request path=%s status=%s queries=%s db_ms=%.1f total_ms=%.1f",
        request.path,
        response.status_code,
        g.get("query_count", 0),
        query_ms,
        total_ms,
    )
    return response


def parse_integer(value, name, *, minimum, maximum):
    try:
        result = int(value)
    except (ValueError, TypeError):
        abort(400, description=f"Invalid {name}.")
    if not minimum <= result <= maximum:
        abort(400, description=f"Invalid {name}.")
    return result


def query_url(path, options, **changes):
    return path + "?" + urlencode(dict(options, **changes))


def database_error(template, context):
    current_app.logger.exception("Database request failed")
    return (
        render_template(template, **context, messages=[], errors=[DATABASE_ERROR]),
        503,
    )


def pagination(options, *, page, per_page, count, result_count):
    offset = (page - 1) * per_page
    start = offset + 1 if result_count else 0
    end = min(offset + result_count, count) if result_count else 0
    return {
        "total_count": count,
        "page": page,
        "message_range": f"{start} - {end}",
        "show_prev": page > 1,
        "show_next": offset + per_page < count,
        "prev_page_url": query_url("/", options, rpp=per_page, page=page - 1),
        "next_page_url": query_url("/", options, rpp=per_page, page=page + 1),
    }


def mark_message_groups(records):
    previous_user, previous_date = None, None
    for record in records:
        new_user = record["username"] != previous_user
        new_date = record["date"] != previous_date
        record["display_date"] = new_date
        # Show the header on a new date even when the same author continues.
        record["display_user"] = new_user or new_date
        previous_user, previous_date = record["username"], record["date"]


def context_summary(anchor):
    zone = ZoneInfo(current_app.config["DISPLAY_TIMEZONE"])
    stamp = datetime.fromtimestamp(anchor["timestamp"], zone)
    formatted_date = stamp.strftime("%I:%M %p %Y-%m-%d (%a)")
    return f"{anchor['full_name']} ({formatted_date}): {anchor['text'] or ''}"


@pages.route("/favicon.ico")
def favicon():
    return current_app.send_static_file("favicon.ico")


@pages.route("/")
def search():
    options = queries.parse_filters(request.args)
    page = parse_integer(
        request.args.get("page", 1), "page", minimum=1, maximum=MAX_OFFSET
    )
    per_page = parse_integer(
        request.args.get("rpp", DEFAULT_PAGE_SIZE),
        "results per page",
        minimum=1,
        maximum=MAX_PAGE_SIZE,
    )
    try:
        rows, count = queries.search(options, page, per_page)
    except ValueError:
        abort(400, description="Invalid date; use YYYY-MM-DD.")
    except Error:
        return database_error("results.j2", options)
    if count is None:
        return render_template("results.j2", **options, messages=[])
    try:
        records = messages.prepare_messages(rows, options["q"])
    except Error:
        return database_error("results.j2", options)
    page_context = pagination(
        options, page=page, per_page=per_page, count=count, result_count=len(rows)
    )
    return render_template("results.j2", **options, **page_context, messages=records)


@pages.route("/context/<message_id>/", methods=["GET", "POST"])
@pages.route("/context/<message_id>/<q>", methods=["GET", "POST"])
@pages.route("/context/<message_id>/<q>/", methods=["GET", "POST"])
def context(message_id, q=None):
    message_id = parse_integer(
        message_id, "message ID", minimum=1, maximum=MAX_MESSAGE_ID
    )
    args = CombinedMultiDict([request.form, request.args])
    options = queries.parse_filters(args)
    if q is not None:
        options["q"] = q
    offset = parse_integer(
        args.get("offset", 0), "offset", minimum=-MAX_OFFSET, maximum=MAX_OFFSET
    )
    base_url = f"/context/{message_id}/"
    template_context = dict(
        options,
        offset=offset,
        message_id=message_id,
        urlpath=base_url,
        back_url=query_url("/", options),
    )
    try:
        anchor, rows = queries.context(message_id, offset)
        if anchor is None:
            abort(404, description="Message not found.")
        records = messages.prepare_messages(rows, options["q"], include_details=True)
    except Error:
        return database_error("context.j2", template_context)
    mark_message_groups(records)
    for name, delta in CONTEXT_STEPS:
        template_context[name] = query_url(base_url, options, offset=offset + delta)
    return render_template(
        "context.j2",
        **template_context,
        messages=records,
        summary=context_summary(anchor),
    )
