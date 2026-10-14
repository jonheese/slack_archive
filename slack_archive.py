"""WSGI entry point, compatible with the existing Flask module name."""
from archive import create_app

app = create_app()
