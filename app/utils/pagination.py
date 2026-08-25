from flask import request


def paginate_query(query, default_per_page=25, max_per_page=100):
    """Applies page/per_page query params and returns the consistent
    pagination envelope described in spec #81."""
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    try:
        per_page = int(request.args.get("per_page", default_per_page))
    except ValueError:
        per_page = default_per_page
    per_page = max(1, min(per_page, max_per_page))

    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    return {
        "items": paginated.items,
        "pagination": {
            "page": paginated.page,
            "per_page": paginated.per_page,
            "total": paginated.total,
            "pages": paginated.pages,
        },
    }
