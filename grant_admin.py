from app import app, db, User
with app.app_context():
    u = User.query.filter_by(username="niansia").first()
    if not u:
        print("找不到這個使用者")
    else:
        u.is_admin = True
        db.session.commit()
        print(f"{u.username} 已經被升級為管理員")
