
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from flask_limiter import Limiter
from app.utils.rate_limit import rate_limit_key

db = SQLAlchemy()
migrate = Migrate()
cors = CORS()
limiter = Limiter(key_func=rate_limit_key)
