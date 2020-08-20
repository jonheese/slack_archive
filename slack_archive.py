# -*- coding: utf-8 -*-

import emoji
import json
import math
import mysql.connector
import re
import sys
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD
from datetime import datetime
from flask import Flask, render_template, Response, request
from mysql.connector import Error
from random import randint

app = Flask(__name__)

reserved_words = [
    "blockquote",
    "b",
    "pre",
    "a",
    "href",
]

def get_db_conn():
    conn = mysql.connector.connect(host=DB_HOST,
                                   database=DB_NAME,
                                   user=DB_USER,
                                   password=DB_PASSWORD)
    return conn


def do_select(query, args=()):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(query % args)
    results = cursor.fetchall()
    cursor.close()
    return results


def get_files(message_id):
    files = []
    file_records = do_select("select name, mimetype, thumb_80, thumb_800, permalink, permalink_public from tbl_files where message_id = %s", (message_id))
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
        tag = "pre class=%s" % tagclass
    else:
        tag = "pre"
    if text.count(delimiter) > 1 and text.count(delimiter) % 2 == 0:
        split_block = text.split(delimiter)
        for i, block in enumerate(split_block):
            if i % 2 == 0:
                continue
            text = text.replace('%s%s%s' % (delimiter, block, delimiter), '<%s>%s</%s>' % (tag, block.strip(), tag.split()[0]))
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
                out_text += "<blockquote>%s</blockquote>%s" % (quote_block, line)
                quote_block = ""
            else:
                out_text += line
    if in_quote:
        out_text += "<blockquote>%s</blockquote>" % quote_block
    return out_text


def format_full_blockquote(text):
    split_text = text.split("&gt;&gt;&gt;")
    header = split_text[0]
    quote = "<blockquote>%s</blockquote>" % '\n'.join(split_text[1:])
    return header + quote


def format_user_mentions_and_emojis(text):
    query = "select slack_user_id, username from tbl_users"
    username_by_slack_user_id = { slack_user_id: username for (slack_user_id, username) in do_select(query) }
    for word in text.split():
        if "<@U" in word and ">" in word:
            start_index = word.index("<@U")
            end_index = word.index(">")
            if end_index > start_index:
                slack_user_id = word[start_index + 2:end_index - start_index].split("|")[0]
                if slack_user_id in username_by_slack_user_id.keys():
                    text = text.replace(word, "@" + username_by_slack_user_id[slack_user_id])
        if word.startswith(":") and word.endswith(":"):
            emoji_trigger = word[1:-1]
            text = text.replace(word, emoji.emojize(emoji_trigger, use_aliases=True))
    return text


def format_links(text):
    for word in text.split():
        if "<" in word and ">" in word:
            start_index = word.index("<")
            end_index = word.index(">")
            if end_index > start_index:
                url = word[start_index + 1:end_index - start_index].split("|")[0]
                text = text.replace(word, "<a href='%s'>%s</a>" % (url, url))
    return text


def format_simple_tag(text, delimiter, tag):
    for word in text.split():
        if word.startswith(delimiter) and word.endswith(delimiter):
            text = text.replace(word, '<%s>%s</%s>' % (tag, word[1:-1], tag))
    return text


def package_messages(messages, q):
    records = []
    for message in messages:
        text = message[5]
        text = format_user_mentions_and_emojis(text)
        text = format_links(text)
        text = format_pre(text, '```')
        text = format_pre(text, '`', tagclass="inline")
        if text.startswith("&gt;&gt;&gt;") or "\n&gt;&gt;&gt;" in text:
            text = format_full_blockquote(text)
        if text.startswith("&gt;") or "\n&gt;" in text:
            text = format_line_blockquote(text)
        text = format_simple_tag(text, '_', 'i')
        text = format_simple_tag(text, '*', 'b')
        text = text.replace('\n', '<br />')
        if q and q not in reserved_words:
            #q_pattern = re.compile(" %s" % q, re.IGNORECASE)
            #text = q_pattern.sub(" <b>%s</b>" % q, text)
            text = text.replace(q, "<b>%s</b>" % q)
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
            "files": get_files(message[6]),
        }
        if not record["channel_name"]:
            record["channel_name"] = "Unknown (%s)" % message[8]
        if not record["username"]:
            record["username"] = "Unknown (%s)" % message[9]
        if not record['full_name']:
            record['full_name'] = "Unknown (%s)" % record['username']
        if message[11]:
            record['avatar_url'] = message[11]
        else:
            record['avatar_url'] = "https://cdn1.iconfinder.com/data/icons/the-basics/100/link-broken-chain-512.png"
        records.append(record)
    return records


def query_context_messages(channel_id, timestamp, direction, comparison, limit, offset):
    query = "select m.team_id, m.channel_id, t.team_name, u.username, m.timestamp, m.text, m.id, c.channel_name, c.slack_channel_id, u.slack_user_id, u.full_name, u.avatar_url from " + \
            "tbl_messages m join tbl_users u on u.id = m.user_id join tbl_channels c on c.id = m.channel_id " + \
            "join tbl_teams t on t.id = m.team_id where m.channel_id = %s and timestamp " + comparison + " %s order by timestamp " + direction + " limit " + \
            str(abs(offset)) + ", " + str(limit)
    print(query)
    return do_select(query, (channel_id, timestamp))


@app.route('/', methods=['GET'])
def search():
    q = request.args.get("q", "")
    username = request.args.get("username", "")
    team_name = request.args.get("team_name", "")
    channel_name = request.args.get("channel_name", "")
    sql_match = request.args.get("sql_match", False)
    page = int(request.args.get("page", 1))
    results_per_page = int(request.args.get("rpp", 20))
    offset = results_per_page * (page - 1)
    try:
        conn = get_db_conn()
        where_clause = "where "
        where_columns = []
        where_values = []
        if username:
            where_columns.append(" (u.username LIKE '%%%s%%' OR u.slack_user_id LIKE '%%%s%%' or u.full_name like '%%%s%%') ")
            where_values.append(username)
            where_values.append(username)
            where_values.append(username)
        if team_name:
            where_columns.append(" t.team_name LIKE '%%%s%%' ")
            where_values.append(team_name)
        if channel_name:
            where_columns.append(" (c.channel_name LIKE '%%%s%%' OR c.slack_channel_id LIKE '%%%s%%') ")
            where_values.append(channel_name)
            where_values.append(channel_name)
        if q:
            if sql_match:
                where_columns.append(" match(m.text) AGAINST('%s') ")
            else:
                where_columns.append(" m.text LIKE '%%%s%%' ")
            where_values.append(q)
        if not where_columns:
            return render_template("results.j2", messages=[])
        where_clause = " WHERE " + " AND ".join(where_columns)
        limit_clause = " LIMIT %s, %s" % (offset, results_per_page)
        count_query = "select count(m.id) from " + \
                "tbl_messages m join tbl_users u on u.id = m.user_id join tbl_channels c on c.id = m.channel_id " + \
                "join tbl_teams t on t.id = m.team_id " + where_clause
        total_count = int(do_select(count_query, tuple(where_values))[0][0])
        query = "select m.team_id, m.channel_id, t.team_name, u.username, m.timestamp, m.text, m.id, c.channel_name, c.slack_channel_id, u.slack_user_id, u.full_name, u.avatar_url from " + \
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
        message_range = "%s - %s" % (range_start, min(offset+results_per_page, total_count))
        param_string = []
        for param_name in request.args.keys():
            if param_name != "page":
                param_string.append("%s=%s" % (param_name, request.args[param_name]))
        param_string = "&".join(param_string)
        prev_page_url = "/?%s&page=%s" % (param_string, page-1)
        next_page_url = "/?%s&page=%s" % (param_string, page+1)
        return render_template("results.j2",
                               q=q,
                               team_name=team_name,
                               channel_name=channel_name,
                               username=username,
                               sql_match=sql_match,
                               show_prev=show_prev,
                               show_next=show_next,
                               prev_page_url=prev_page_url,
                               next_page_url=next_page_url,
                               page=page,
                               message_range=message_range,
                               total_count=total_count,
                               messages=messages)
    except Error as e:
        return render_template("results.j2", q="Failed to connect to db: %s" % e)


@app.route('/context/<message_id>/', methods=['GET'])
@app.route('/context/<message_id>/<q>', methods=['GET'])
@app.route('/context/<message_id>/<q>/', methods=['GET'])
@app.route('/context/<message_id>/<q>/<offset>', methods=['GET'])
def context(message_id, q="", offset=0):
    try:
        query = "select team_id, channel_id, timestamp from tbl_messages where id = %s"
        messages = do_select(query, message_id)
        team_id = messages[0][0]
        channel_id = messages[0][1]
        timestamp = messages[0][2]
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
        username = ""
        color = "#ffffff"
        date = None
        for message in messages:
            if message["username"] != username:
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
            username = message["username"]
        return render_template("context.j2", messages=messages, q=q, offset=offset, message_id=message_id)
    except Error as e:
        return render_template("context.j2", errors=["Database query error: %s" % e], messages=None, q=q, offset=offset, message_id=message_id)
