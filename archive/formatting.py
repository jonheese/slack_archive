"""Render stored Slack text; only generated, escaped HTML is trusted."""

import html
import re
from urllib.parse import urlsplit

import emoji
from markupsafe import Markup, escape

SLACK_TOKEN = re.compile(r"```[\s\S]*?```|`[^`\n]+`|<[^>\n]+>|:[A-Za-z0-9_+\-]+:")
CODE_BLOCK = re.compile(r"(```[\s\S]*?```)")
EMPHASIS = re.compile(r"(\*[^*\n]+\*|_[^_\n]+_)")
BROKEN_IMAGE = (
    "https://cdn1.iconfinder.com/data/icons/the-basics/100/link-broken-chain-512.png"
)
LINK_SCHEMES = {"http", "https", "mailto"}
IMAGE_SCHEMES = {"http", "https"}


def safe_url(value, image=False):
    value = html.unescape(str(value or "")).strip()
    if any(ord(char) < 32 for char in value):
        return ""
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return ""
    allowed_schemes = IMAGE_SCHEMES if image else LINK_SCHEMES
    return value if scheme in allowed_schemes else ""


def first_safe_url(candidates, *, image=False):
    """Choose the first usable URL, validating each candidate just once."""
    for candidate in candidates:
        url = safe_url(candidate, image=image)
        if url:
            return url
    return BROKEN_IMAGE


class MessageRenderer:
    """Keep one message's lookups/options together while rendering its parts."""

    def __init__(self, users, emojis, *, query="", is_image=False, spoiler=False):
        self.users = users
        self.emojis = emojis
        self.is_image = is_image
        self.spoiler = spoiler
        self.highlight_pattern = None
        if query:
            self.highlight_pattern = re.compile(
                "(" + re.escape(query) + ")", re.IGNORECASE
            )

    def highlight_text(self, text):
        if self.highlight_pattern is None:
            return str(escape(text))
        output = []
        for index, part in enumerate(self.highlight_pattern.split(text)):
            escaped = str(escape(part))
            output.append(f"<b>{escaped}</b>" if index % 2 else escaped)
        return "".join(output)

    def render_plain_text(self, text):
        # Emphasis/highlighting never touch generated attributes or code.
        output = []
        for part in EMPHASIS.split(text):
            tag = None
            if len(part) > 2 and part[0] == part[-1] and part[0] in "*_":
                tag = "b" if part[0] == "*" else "i"
                part = part[1:-1]
            rendered = self.highlight_text(part)
            output.append(f"<{tag}>{rendered}</{tag}>" if tag else rendered)
        return "".join(output)

    def render_mention(self, token):
        user_id = token[2:-1].split("|", 1)[0]
        username = self.users.get(user_id) or user_id
        return str(escape("@" + username))

    def render_link(self, token):
        target, _, label = token[1:-1].partition("|")
        url = safe_url(target)
        if not url:
            return str(escape(token))
        rendered = f'<a href="{escape(url)}">{escape(label or target)}</a>'
        if self.is_image and safe_url(url, image=True):
            rendered += f'<br /><img src="{escape(url)}" />'
        return rendered

    def render_emoji(self, token):
        url = safe_url(self.emojis.get(token[1:-1]), image=True)
        if url:
            return f'<img height="22px" width="22px" src="{escape(url)}" />'
        return str(escape(emoji.emojize(token, language="alias")))

    def render_token(self, token):
        if token.startswith("```"):
            return f"<pre>{escape(token[3:-3].strip())}</pre>"
        if token.startswith("`"):
            return f'<pre class="inline">{escape(token[1:-1].strip())}</pre>'
        if token.startswith("<@"):
            return self.render_mention(token)
        if token.startswith("<"):
            return self.render_link(token)
        return self.render_emoji(token)

    def render_inline(self, text):
        output = []
        position = 0
        for match in SLACK_TOKEN.finditer(text):
            output.append(self.render_plain_text(text[position : match.start()]))
            output.append(self.render_token(match.group()))
            position = match.end()
        output.append(self.render_plain_text(text[position:]))
        return "".join(output)

    def render_line(self, line):
        if self.spoiler and ":" in line:
            prefix, content = line.split(":", 1)
            return (
                self.render_inline(prefix + ":")
                + '<span class="spoiler">'
                + self.render_inline(content)
                + "</span>"
            )
        return self.render_inline(line)

    def render_quoted_lines(self, text):
        output = []
        in_quote = False
        quote_to_end = False
        for line in text.split("\n"):
            if line.startswith(">>>"):
                if not in_quote:
                    output.append("<blockquote>")
                in_quote = quote_to_end = True
                line = line[3:]
            elif line.startswith(">") and not quote_to_end:
                if not in_quote:
                    output.append("<blockquote>")
                in_quote = True
                line = line[1:]
            elif in_quote and not quote_to_end:
                output.append("</blockquote>")
                in_quote = False
            output.extend((self.render_line(line), "<br />"))
        output.pop()  # Remove the trailing line break, keeping breaks inside quotes.
        if in_quote:
            output.append("</blockquote>")
        return "".join(output)

    def render(self, text):
        output = []
        # Protect multiline code before interpreting line-based blockquotes.
        for block in CODE_BLOCK.split(html.unescape(text or "")):
            if block.startswith("```") and block.endswith("```"):
                output.append(self.render_inline(block))
            else:
                output.append(self.render_quoted_lines(block))
        return Markup("".join(output))


def render_text(text, users, emojis, query="", is_image=False, spoiler=False):
    renderer = MessageRenderer(
        users, emojis, query=query, is_image=is_image, spoiler=spoiler
    )
    return renderer.render(text)
