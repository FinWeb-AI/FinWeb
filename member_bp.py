from flask import Blueprint

member_bp = Blueprint(
    "member", __name__,
    template_folder="member",
    url_prefix="/member"
)