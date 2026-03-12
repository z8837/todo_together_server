class AppPerDatabaseRouter:
    # 앱 → DB 명시 매핑 (여기에만 허용, 나머지는 전부 불허)
    APP_DB_MAP = {
        "firstapp": "firstapp_db",
        "todo_together": "toto_db",      # 예시: 너의 실제 앱 라벨
        # 시스템 앱은 보통 default로 고정
        "auth": "default",
        "contenttypes": "default",
        "admin": "default",
        "sessions": "default",
    }

    def _app_label(self, model_or_label):
        return (model_or_label if isinstance(model_or_label, str)
                else model_or_label._meta.app_label)

    def db_for_read(self, model, **hints):
        label = self._app_label(model)
        return self.APP_DB_MAP.get(label)

    def db_for_write(self, model, **hints):
        label = self._app_label(model)
        return self.APP_DB_MAP.get(label)

    def allow_relation(self, obj1, obj2, **hints):
        db1 = self.APP_DB_MAP.get(self._app_label(type(obj1)))
        db2 = self.APP_DB_MAP.get(self._app_label(type(obj2)))
        if db1 and db2:
            return db1 == db2
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        target = self.APP_DB_MAP.get(app_label)
        if target is None:
            # 매핑 없는 앱은 전부 금지(또는 default로 몰고 싶으면 return db == "default")
            return False
        return db == target