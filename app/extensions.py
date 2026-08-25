"""
Shared extension instances, created here (not in __init__.py) so models
and routes can import `db` without triggering circular imports.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS

db = SQLAlchemy()
migrate = Migrate()
cors = CORS()
