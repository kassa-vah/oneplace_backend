from flask import jsonify
from werkzeug.exceptions import HTTPException


def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def handle_http_exception(err):
        return jsonify({"error": err.description or err.name}), err.code

    @app.errorhandler(Exception)
    def handle_unexpected_exception(err):
        # Full detail goes to the server log only. The client never sees
        # a stack trace, SQL statement, or file path (spec #92).
        app.logger.exception("Unhandled exception")
        return jsonify({"error": "An unexpected error occurred"}), 500
