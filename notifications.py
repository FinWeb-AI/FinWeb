from datetime import datetime
from flask import render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Optional, URL, Length

from member_bp import member_bp
import sys
if 'app' in sys.modules:
    from app import db, Notification, User
else:
    main_app = sys.modules['__main__']
    db           = main_app.db
    Notification = main_app.Notification
    User         = main_app.User

class NotificationForm(FlaskForm):
    title   = StringField("通知標題", validators=[DataRequired(), Length(max=200)])
    body    = TextAreaField("通知內容", validators=[DataRequired()])
    link    = StringField("連結 (可選)", validators=[Optional(), URL()])
    submit  = SubmitField("發布通知")

@member_bp.route("/notifications/new", methods=["GET", "POST"])
@login_required
def new_notification():
    if not current_user.is_admin:
        abort(403)

    form = NotificationForm()
    if form.validate_on_submit():
        title = form.title.data
        body  = form.body.data
        link  = form.link.data or None

        all_ids = [u.id for u in User.query.with_entities(User.id).all()]
        for uid in all_ids:
            n = Notification(
                user_id    = uid,
                title      = title,
                body       = body,
                link       = link,
                created_at = datetime.utcnow()
            )
            db.session.add(n)
        db.session.commit()

        flash("✅ 通知已群發完成", "success")
        return redirect(url_for("member.notifications"))

    return render_template("member/new_notification.html", form=form)


@member_bp.route("/notifications/delete/<int:note_id>", methods=["POST"])
@login_required
def delete_notification(note_id):
    if not current_user.is_admin:
        abort(403)
    n = Notification.query.get_or_404(note_id)
    db.session.delete(n)
    db.session.commit()
    flash("⚠️ 通知已刪除", "warning")
    return redirect(url_for("member.notifications"))
