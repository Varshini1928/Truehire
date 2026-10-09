"""Management commands do not start a second scheduler or interrupt scan jobs."""

import sys
from app import create_app

if __name__ == "__main__":
    app = create_app({"ENABLE_SCHEDULER": False})
    with app.app_context():
        app.cli.main(args=sys.argv[1:], prog_name="python manage.py")
