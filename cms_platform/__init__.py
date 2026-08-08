"""Project package.

The interpreter guard runs here so it also covers the entry points that never
touch ``manage.py`` — wsgi/asgi under gunicorn, and anything that imports
``cms_platform.settings`` directly. See ``cms_platform/python_version.py``.
"""

from cms_platform.python_version import enforce_python_version

enforce_python_version()
