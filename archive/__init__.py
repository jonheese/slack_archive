"""Slack archive application factory."""

import logging
from pathlib import Path
from flask import Flask


def create_app(config=None):
    root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.config.from_mapping(DISPLAY_TIMEZONE="America/New_York", DB_PORT=3306)
    if config is None:
        app.config.from_pyfile(str(root / "config.py"))
    else:
        app.config.update(config)
    # Flask does not autoescape .j2 templates by default.
    app.jinja_env.autoescape = True
    app.logger.setLevel(logging.INFO)
    from . import db
    from .views import pages

    app.teardown_appcontext(db.close_connection)
    app.register_blueprint(pages)
    return app
