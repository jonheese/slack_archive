# -*- coding: utf-8 -*-

import emoji
import json
import math
import mysql.connector
import re
import sys
import urllib.parse
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD
from datetime import datetime
from flask import Flask, render_template, Response, request, send_file
from mysql.connector import Error
from random import randint

app = Flask(__name__)

reserved_words = [
    "blockquote",
    "b",
    "pre",
    "a",
    "href",
    "img",
]


def set_url_param(
    urlpath,
    param_name,
    param_value,
):
    param_dict = {}
    param_value = str(param_value)
    if "?" in urlpath:
        [url, query] = urlpath.split("?")
    else:
        url = urlpath
        query = ""
    for pair in query.split("&"):
        key = value = None
        if "=" in pair:
            [key, value] = pair.split("=")
        if key and value:
            param_dict[key] = value
    param_dict[param_name] = urllib.parse.quote(param_value).replace("%20", "+")
    url = url + "?"
    for key, value in param_dict.items():
        url = url + f"{key}={value}&"
    return url[:-1]


def get_db_conn():
    conn = mysql.connector.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    return conn


def do_select(query, args=()):
    conn = get_db_conn()
    cursor = conn.cursor()
    print(f'Query: {query % args}')
    cursor.execute(query, args)
    results = cursor.fetchall()
    cursor.close()
    return results


def get_files(message_id):
    files = []
    file_records = do_select(
        "select name, mimetype, thumb_80, thumb_800, permalink, permalink_public from tbl_files where message_id = %(message_id)s",
        {
            'message_id': message_id
        }
    )
    for file_record in file_records:
        file = {
            "name": file_record[0],
            "mimetype": file_record[1],
        }
        (thumb_80, thumb_800, permalink, permalink_public) = tuple(file_record[2:])
        if permalink_public:
            file['url'] = permalink_public
        if thumb_80:
            file['thumb'] = thumb_80
        if thumb_800:
            if 'url' not in file.keys():
                file['url'] = thumb_800
            if 'thumb' not in file.keys():
                file['thumb'] = thumb_800
        if permalink:
            if 'url' not in file.keys():
                file['url'] = permalink
            if 'thumb' not in file.keys():
                file['thumb'] = permalink
        if 'url' not in file.keys():
            file['url'] = "https://cdn1.iconfinder.com/data/icons/the-basics/100/link-broken-chain-512.png"
        if 'thumb' not in file.keys():
            file['thumb'] = "https://cdn1.iconfinder.com/data/icons/the-basics/100/link-broken-chain-512.png"
        files.append(file)
    return files


def format_pre(text, delimiter, tagclass=None):
    if tagclass:
        tag = f"pre class={tagclass}"
    else:
        tag = "pre"
    if text.count(delimiter) > 1 and text.count(delimiter) % 2 == 0:
        split_block = text.split(delimiter)
        for i, block in enumerate(split_block):
            if i % 2 == 0:
                continue
            text = text.replace(f'{delimiter}{block}{delimiter}', f'<{tag}>{block.strip()}</{tag.split()[0]}>')
    return text


def format_line_blockquote(text):
    out_text = ""
    in_quote = False
    quote_block = ""
    for line in text.split('\n'):
        if line.startswith('&gt;'):
            if not in_quote:
                in_quote = True
                quote_block += line.replace("&gt;", "")
            else:
                quote_block += "\n" + line.replace("&gt;", "")
        else:
            if in_quote:
                in_quote = False
                out_text += f"<blockquote>{quote_block}</blockquote>{line}"
                quote_block = ""
            else:
                out_text += line
    if in_quote:
        out_text += f"<blockquote>{quote_block}</blockquote>"
    return out_text


def format_spoilers(text):
    spoiler_text = ":".join(text.split(':')[1:])
    if spoiler_text.startswith(" "):
        return text.replace(spoiler_text, f' <span class="spoiler">{"".join(spoiler_text[1:])}</span>')
    return text.replace(spoiler_text, f'<span class="spoiler">{spoiler_text}</span>')


def format_full_blockquote(text):
    split_text = text.split("&gt;&gt;&gt;")
    header = split_text[0]
    lines = '\n'.join(split_text[1:])
    quote = f"<blockquote>{lines}</blockquote>"
    return header + quote


def format_user_mentions(text):
    query = "select slack_user_id, username from tbl_users"
    username_by_slack_user_id = { slack_user_id: username for (slack_user_id, username) in do_select(query) }
    for word in text.split():
        if "<@U" in word and ">" in word:
            start_index = word.index("<@U")
            end_index = word.index(">")
            if end_index > start_index:
                slack_user_id = word[start_index + 2:end_index - start_index].split("|")[0]
                if slack_user_id in username_by_slack_user_id.keys() and username_by_slack_user_id[slack_user_id]:
                    text = text.replace(word, "@" + username_by_slack_user_id[slack_user_id])
                else:
                    text = text.replace(word, "@" + slack_user_id)
    return text


def format_emojis(text):
    query = "select emoji_trigger, url from tbl_emojis"
    url_by_emoji_trigger = { emoji_trigger: url for (emoji_trigger, url) in do_select(query) }
    for word in text.split():
        if word.startswith(":") and word.endswith(":"):
            emoji_trigger = word[1:-1]
            emoji_text = url_by_emoji_trigger.get(emoji_trigger)
            if emoji_text is not None:
                text = text.replace(word, f"<img height='22px' width='22px' src='{emoji_text}' />")
                continue
            emoji_text = emoji.emojize(emoji_trigger, use_aliases=True)
            if emoji_text != emoji_trigger:
                text = text.replace(word, emoji_text)
    return text


def format_links(text, is_image):
    for word in text.split():
        if "<" in word and ">" in word:
            start_index = word.index("<")
            end_index = word.index(">")
            if end_index > start_index:
                url = word[start_index + 1:end_index - start_index].split("|")[0]
                text = text.replace(word, f"<a href='{url}'>{url}</a>")
                if is_image:
                    text += f"<br /><img src='{url}' />"
    return text


def format_simple_tag(text, delimiter, tag):
    if delimiter == "*":
        delimiter = "\*"
    return re.sub(f'{delimiter}(.*?){delimiter}', f'<{tag}>\\1</{tag}>', text)


def package_messages(messages, q):
    records = []
    for message in messages:
        text = message[5]
        text = format_user_mentions(text)
        text = format_emojis(text)
        if len(message) >= 14:
            text = format_links(text, message[13])
        else:
            text = format_links(text, False)
        text = format_pre(text, '```')
        text = format_pre(text, '`', tagclass="inline")
        if text.startswith("&gt;&gt;&gt;") or "\n&gt;&gt;&gt;" in text:
            text = format_full_blockquote(text)
        if text.startswith("&gt;") or "\n&gt;" in text:
            text = format_line_blockquote(text)
        text = format_simple_tag(text, '_', 'i')
        text = format_simple_tag(text, '*', 'b')
        text = text.replace('\n', '<br />')
        if message[3] == 'spoiler_alert':
            text = format_spoilers(text)
        if q and q not in reserved_words:
            boldify = True
            for reserved_word in reserved_words:
                if reserved_word in text:
                    boldify = False
                    break
            if boldify:
                #q_pattern = re.compile(f" {q}", re.IGNORECASE)
                #text = q_pattern.sub(f" <b>{q}</b>", text)
                text = text.replace(q, f"<b>{q}</b>")
        record = {
            "team_id": message[0],
            "channel_id": message[1],
            "team_name": message[2],
            "username": message[3],
            "date": datetime.fromtimestamp(message[4]).strftime('%Y-%m-%d (%a)'),
            "time": datetime.fromtimestamp(message[4]).strftime('%I:%M %p'),
            "text": text,
            "id": message[6],
            "channel_name": message[7],
            "full_name": message[10],
            "is_image": None,
            "files": get_files(message[6]),
            "archive_url": None,
        }
        if len(message) >= 13:
            record["archive_url"] = message[12]
        if len(message) >= 14:
            record["is_image"] = message[13]
        if not record["channel_name"]:
            record["channel_name"] = f"Unknown ({message[8]})"
        if not record["username"]:
            record["username"] = f"Unknown ({message[9]})"
        if not record['full_name']:
            record['full_name'] = f"Unknown ({record['username']})"
        if message[11]:
            record['avatar_url'] = message[11]
        else:
            record['avatar_url'] = "https://cdn1.iconfinder.com/data/icons/the-basics/100/link-broken-chain-512.png"

        record['reactions'] = get_message_reactions(message[6])
        records.append(record)
    return records


def get_message_reactions(message_id=None):
    query = 'select e.emoji_trigger, e.url, u.username from tbl_reactions r join tbl_emojis e on ' + \
            'r.emoji_id = e.id join tbl_users u on r.user_id = u.id where r.message_id = %(message_id)s'
    return do_select(
        query,
        {
            'message_id': message_id
        }
    )

def query_context_messages(channel_id, timestamp, direction, comparison, limit, offset):
    query = "select m.team_id, m.channel_id, t.team_name, u.username, m.timestamp, m.text, m.id, " + \
            "c.channel_name, c.slack_channel_id, u.slack_user_id, u.full_name, u.avatar_url, m.archive_url, m.is_image from " + \
            "tbl_messages m join tbl_users u on u.id = m.user_id join tbl_channels c on c.id = m.channel_id " + \
            "join tbl_teams t on t.id = m.team_id where m.channel_id = %(channel_id)s and timestamp " + comparison + \
            " %(timestamp)s order by timestamp " + direction + " limit " + str(abs(offset)) + ", " + str(limit)
    return do_select(
        query,
        {
            'channel_id': channel_id,
            'timestamp': timestamp
        }
    )


@app.route('/favicon.ico', methods=['GET'])
def get_favicon():
    return send_file('static/favicon.ico')


@app.route('/', methods=['GET'])
def search():
    q = request.args.get("q", "")
    print(q)
    username = request.args.get("username", "").replace("'", "\'")
    team_name = request.args.get("team_name", "").replace("'", "\'")
    channel_name = request.args.get("channel_name", "").replace("'", "\'")
    start_date = request.args.get("start_date", "").replace("'", "\'")
    end_date = request.args.get("end_date", "").replace("'", "\'")
    sql_match = request.args.get("sql_match", False)
    case_sensitive = request.args.get("case_sensitive", False)
    page = int(request.args.get("page", 1))
    results_per_page = int(request.args.get("rpp", 20))
    offset = results_per_page * (page - 1)
    try:
        conn = get_db_conn()
        where_clause = "where "
        where_columns = []
        where_values = []
        if username:
            if username.startswith("-"):
                where_columns.append(" (u.username NOT LIKE '%%%s%%' AND u.slack_user_id NOT LIKE '%%%s%%' AND u.full_name NOT like '%%%s%%') ")
                where_values.extend([username[1:], username[1:], username[1:]])
            else:
                where_columns.append(" (u.username LIKE '%%%s%%' OR u.slack_user_id LIKE '%%%s%%' or u.full_name like '%%%s%%') ")
                where_values.extend([username, username, username])
        if team_name:
            if team_name.startswith("-"):
                where_columns.append(" t.team_name NOT LIKE '%%%s%%' ")
                where_values.extend(team_name[1:])
            else:
                where_columns.append(" t.team_name LIKE '%%%s%%' ")
                where_values.append(team_name)
        if channel_name:
            if channel_name.startswith("-"):
                where_columns.append(" (c.channel_name NOT LIKE '%%%s%%' AND c.slack_channel_id NOT LIKE '%%%s%%') ")
                where_values.extend([channel_name[1:], channel_name[1:]])
            else:
                where_columns.append(" (c.channel_name LIKE '%%%s%%' OR c.slack_channel_id LIKE '%%%s%%') ")
                where_values.extend([channel_name, channel_name])
        if start_date:
            where_columns.append(" m.timestamp > %s ")
            where_values.append(datetime.strptime(start_date, "%Y-%m-%d").strftime("%s"))
        if end_date:
            where_columns.append(" m.timestamp < %s ")
            where_values.append(datetime.strptime(end_date, "%Y-%m-%d").strftime("%s"))
        if q:
            if sql_match:
                where_columns.append(" match(m.text) AGAINST('%s') ")
            elif case_sensitive:
                where_columns.append(" BINARY m.text LIKE '%%%s%%' ")
            else:
                where_columns.append(" m.text LIKE '%%%s%%' ")
            where_values.append(q)
        if not where_columns:
            return render_template("results.j2", messages=[])
        where_clause = " WHERE " + " AND ".join(where_columns)
        limit_clause = f" LIMIT {offset}, {results_per_page}"
        count_query = "select count(m.id) from " + \
                "tbl_messages m join tbl_users u on u.id = m.user_id join tbl_channels c on c.id = m.channel_id " + \
                "join tbl_teams t on t.id = m.team_id " + where_clause
        total_count = int(
            do_select(
                count_query,
                tuple(where_values)
            )[0][0]
        )
        query = "select m.team_id, m.channel_id, t.team_name, u.username, m.timestamp, m.text, m.id, c.channel_name, " + \
                "c.slack_channel_id, u.slack_user_id, u.full_name, u.avatar_url, m.archive_url, m.is_image from " + \
                "tbl_messages m join tbl_users u on u.id = m.user_id join tbl_channels c on c.id = m.channel_id " + \
                "join tbl_teams t on t.id = m.team_id " + where_clause + " order by timestamp " + limit_clause
        messages = package_messages(do_select(query, tuple(where_values)), q)
        if total_count > results_per_page:
            show_prev = page != 1
            show_next = page * results_per_page < total_count
        else:
            show_prev = False
            show_next = False
        if total_count == 0:
            range_start = 0
        else:
            range_start = offset + 1
        message_range = f"{range_start} - {min(offset+results_per_page, total_count)}"
        param_string = []
        for param_name in request.args.keys():
            if param_name != "page":
                param_value = request.args[param_name].replace(' ', '+')
                param_string.append(f"{param_name}={param_value}")
        param_string = "&".join(param_string)
        prev_page_url = f"/?{param_string}&page={page-1}"
        next_page_url = f"/?{param_string}&page={page+1}"
        return render_template("results.j2",
                               q=q,
                               team_name=team_name,
                               channel_name=channel_name,
                               username=username,
                               start_date=start_date,
                               end_date=end_date,
                               sql_match=sql_match,
                               case_sensitive=case_sensitive,
                               show_prev=show_prev,
                               show_next=show_next,
                               prev_page_url=prev_page_url,
                               next_page_url=next_page_url,
                               page=page,
                               message_range=message_range,
                               total_count=total_count,
                               param_string=param_string,
                               messages=messages)
    except Error as e:
        return render_template("results.j2")


@app.route('/context/<message_id>/', methods=['GET', 'POST'])
@app.route('/context/<message_id>/<q>', methods=['GET', 'POST'])
@app.route('/context/<message_id>/<q>/', methods=['GET', 'POST'])
def context(message_id, q=None):
    try:
        if not q:
            q = request.form.get("q", request.args.get("q", ""))
        offset = request.form.get("offset", request.args.get("offset", 0))
        username = request.form.get("username", request.args.get("username", ""))
        channel_name = request.form.get("channel_name", request.args.get("channel_name", ""))
        team_name = request.form.get("team_name", request.args.get("team_name", ""))
        start_date = request.form.get("start_date", request.args.get("start_date", ""))
        end_date = request.form.get("end_date", request.args.get("end_date", ""))
        sql_match = request.form.get("sql_match", request.args.get("sql_match", False)) == "True"
        case_sensitive = request.form.get("case_sensitive", request.args.get("case_sensitive", False)) == "True"

        query = "select m.team_id, m.channel_id, m.timestamp, m.text, u.full_name from " + \
                "tbl_messages m join tbl_users u on u.id = m.user_id where m.id = %s"
        messages = do_select(query, message_id)
        team_id = messages[0][0]
        channel_id = messages[0][1]
        timestamp = messages[0][2]
        datetime_string = datetime.fromtimestamp(timestamp).strftime('%I:%M %p %Y-%m-%d (%a)')
        summary = f"{messages[0][4]} ({datetime_string}): {messages[0][3]}".replace('"', "&quot;")
        window_size = 20
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
        half_window = int(math.floor(window_size / 2))
        sql_offset = max(abs(offset) - half_window, 0)
        before_limit = min(half_window - offset, window_size)
        if before_limit > 0:
            messages = package_messages(query_context_messages(channel_id, timestamp, "desc", "<=", before_limit, sql_offset), q)
            messages.reverse()
        else:
            messages = []
        after_limit = min(half_window + offset, window_size)
        if after_limit > 0:
            after_messages = package_messages(query_context_messages(channel_id, timestamp, "asc", ">", after_limit, sql_offset), q)
            messages.extend(after_messages)
        this_username = ""
        color = "#ffffff"
        date = None
        for message in messages:
            if message["username"] != this_username:
                if color == "#ffffff":
                    message["color"] = "#cccccc"
                else:
                    message["color"] = "#ffffff"
                message["display_user"] = True
            else:
                message["color"] = color
                message["display_user"] = False
            message["display_date"] = message["date"] != date
            date = message["date"]
            color = message["color"]
            this_username = message["username"]
        urlpath = request.path

        prev20_url = set_url_param(urlpath, "offset", offset - 20)
        prev_url = set_url_param(urlpath, "offset", offset - 1)
        next_url = set_url_param(urlpath, "offset", offset + 1)
        next20_url = set_url_param(urlpath, "offset", offset + 20)

        return render_template(
            "context.j2",
            messages=messages,
            q=q,
            offset=offset,
            message_id=message_id,
            summary=summary,
            urlpath=urlpath,
            next_url=next_url,
            prev_url=prev_url,
            next20_url=next20_url,
            prev20_url=prev20_url,
            username=username,
            team_name=team_name,
            channel_name=channel_name,
            sql_match=sql_match,
            case_sensitive=case_sensitive,
            start_date=start_date,
            end_date=end_date,
        )
    except Error as e:
        return render_template(
            "context.j2",
            errors=[f"Database query error: {e}"],
            messages=None,
            urlpath=urlpath,
            q=q,
            offset=offset,
            message_id=message_id,
            username=username,
            channel_name=channel_name,
            team_name=team_name,
            sql_match=sql_match,
            case_sensitive=case_sensitive,
            start_date=start_date,
            end_date=end_date,
        )
