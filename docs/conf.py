"""Sphinx configuration for portability-server documentation."""
import os
import sys
import django
from pathlib import Path

sys.path.insert(0, os.path.abspath('..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portability_server.settings')
django.setup()

project = 'portability-server'
copyright = '2024-2026, Aalto RSE'
author = 'Aalto RSE'

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
]

templates_path = ['_templates']
exclude_patterns = ['_build']

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

autodoc_member_order = 'bysource'
autodoc_default_options = {
    'members': True,
    'show-inheritance': True,
}

# -- Auto-generate module docs with sphinx-apidoc on each build --------------

APIDOC_EXCLUDE = [
    '*/migrations/*',
    '*/tests*.py',
    '*/admin.py',
    'portability_server/asgi.py',
    'portability_server/wsgi.py',
    'manage.py',
]


def run_apidoc(_):
    from sphinx.ext.apidoc import main as apidoc_main
    root = str(Path(__file__).resolve().parent.parent)
    out = str(Path(__file__).resolve().parent)
    args = ['-o', out, root, '--separate', '-f'] + [
        os.path.join(root, p) for p in APIDOC_EXCLUDE
    ]
    apidoc_main(args)


def setup(app):
    app.connect('builder-inited', run_apidoc)
