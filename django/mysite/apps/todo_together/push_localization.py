"""Fixed push copy. Event arguments are immutable, JSON-safe enqueue snapshots."""

SUPPORTED_LOCALES = ("ko", "en", "ja", "es", "pt-BR", "fr", "de")


def normalize_push_locale(value):
    if not value or not str(value).strip():
        return "ko"
    language = str(value).strip().replace("_", "-").lower().split("-")[0]
    if language == "pt":
        return "pt-BR"
    return language if language in SUPPORTED_LOCALES else "en"


# Each entry contains invite, project update/delete, TODO create/update/delete,
# TODO reminder and schedule reminder title/body pairs, in that order.
_COPY = {
    "ko": [
        ("'{project}' 프로젝트에 초대되었습니다", "{actor} 님이 초대했습니다."),
        ("'{project}' 프로젝트가 수정되었습니다", "{actor} 님이 '{project}'을 수정했습니다."),
        ("'{project}' 프로젝트가 삭제되었습니다", "{actor} 님이 '{project}' 프로젝트를 삭제했습니다."),
        ("[{project}] 새 TODO가 생성되었습니다", "{actor} 님이 '{todo}'를 추가했습니다."),
        ("[{project}] TODO가 수정되었습니다", "{actor} 님이 '{todo}'를 수정했습니다."),
        ("[{project}] TODO가 삭제되었습니다", "{actor} 님이 '{todo}'를 삭제했습니다."),
        ("[{project}] TODO 알림", "곧 마감될 TODO: {todo}"),
        ("[{project}] 일정 알림", "곧 시작할 일정: {todo}"),
    ],
    "en": [
        ("You’re invited to '{project}'", "{actor} invited you."),
        ("Project '{project}' updated", "{actor} updated '{project}'."),
        ("Project '{project}' deleted", "{actor} deleted project '{project}'."),
        ("[{project}] New TODO", "{actor} added '{todo}'."),
        ("[{project}] TODO updated", "{actor} updated '{todo}'."),
        ("[{project}] TODO deleted", "{actor} deleted '{todo}'."),
        ("[{project}] TODO reminder", "TODO due soon: {todo}"),
        ("[{project}] Schedule reminder", "Starting soon: {todo}"),
    ],
    "ja": [
        ("「{project}」に招待されました", "{actor}さんからの招待です。"),
        ("プロジェクト「{project}」が更新されました", "{actor}さんが「{project}」を更新しました。"),
        ("プロジェクト「{project}」が削除されました", "{actor}さんがプロジェクト「{project}」を削除しました。"),
        ("[{project}] 新しいTODO", "{actor}さんが「{todo}」を追加しました。"),
        ("[{project}] TODOが更新されました", "{actor}さんが「{todo}」を更新しました。"),
        ("[{project}] TODOが削除されました", "{actor}さんが「{todo}」を削除しました。"),
        ("[{project}] TODOリマインダー", "まもなく期限のTODO: {todo}"),
        ("[{project}] 予定のリマインダー", "まもなく開始する予定: {todo}"),
    ],
    "es": [
        ("Te han invitado a '{project}'", "{actor} te ha invitado."),
        ("Proyecto '{project}' actualizado", "{actor} ha actualizado '{project}'."),
        ("Proyecto '{project}' eliminado", "{actor} ha eliminado el proyecto '{project}'."),
        ("[{project}] Nueva tarea", "{actor} ha añadido '{todo}'."),
        ("[{project}] Tarea actualizada", "{actor} ha actualizado '{todo}'."),
        ("[{project}] Tarea eliminada", "{actor} ha eliminado '{todo}'."),
        ("[{project}] Recordatorio de tarea", "Tarea que vence pronto: {todo}"),
        ("[{project}] Recordatorio de evento", "Empieza pronto: {todo}"),
    ],
    "pt-BR": [
        ("Você recebeu um convite para '{project}'", "{actor} convidou você."),
        ("Projeto '{project}' atualizado", "{actor} atualizou '{project}'."),
        ("Projeto '{project}' excluído", "{actor} excluiu o projeto '{project}'."),
        ("[{project}] Nova tarefa", "{actor} adicionou '{todo}'."),
        ("[{project}] Tarefa atualizada", "{actor} atualizou '{todo}'."),
        ("[{project}] Tarefa excluída", "{actor} excluiu '{todo}'."),
        ("[{project}] Lembrete de tarefa", "Tarefa com prazo próximo: {todo}"),
        ("[{project}] Lembrete de evento", "Começa em breve: {todo}"),
    ],
    "fr": [
        ("Vous êtes invité à '{project}'", "{actor} vous a invité."),
        ("Projet '{project}' modifié", "{actor} a modifié '{project}'."),
        ("Projet '{project}' supprimé", "{actor} a supprimé le projet '{project}'."),
        ("[{project}] Nouvelle tâche", "{actor} a ajouté '{todo}'."),
        ("[{project}] Tâche modifiée", "{actor} a modifié '{todo}'."),
        ("[{project}] Tâche supprimée", "{actor} a supprimé '{todo}'."),
        ("[{project}] Rappel de tâche", "Tâche bientôt à échéance : {todo}"),
        ("[{project}] Rappel d’événement", "Commence bientôt : {todo}"),
    ],
    "de": [
        ("Einladung zu '{project}'", "{actor} hat dich eingeladen."),
        ("Projekt '{project}' aktualisiert", "{actor} hat '{project}' aktualisiert."),
        ("Projekt '{project}' gelöscht", "{actor} hat das Projekt '{project}' gelöscht."),
        ("[{project}] Neue Aufgabe", "{actor} hat '{todo}' hinzugefügt."),
        ("[{project}] Aufgabe aktualisiert", "{actor} hat '{todo}' aktualisiert."),
        ("[{project}] Aufgabe gelöscht", "{actor} hat '{todo}' gelöscht."),
        ("[{project}] Aufgabenerinnerung", "Bald fällige Aufgabe: {todo}"),
        ("[{project}] Terminerinnerung", "Beginnt bald: {todo}"),
    ],
}
EVENT_KEYS = ("project_invite", "project_updated", "project_destroy", "todo_created", "todo_updated", "todo_destroy", "reminder_todo", "reminder_schedule")
PUSH_TEMPLATES = {locale: dict(zip(EVENT_KEYS, entries)) for locale, entries in _COPY.items()}


def render_push_message(event_key, arguments, locale):
    title, body = PUSH_TEMPLATES[normalize_push_locale(locale)][event_key]
    return title.format(**arguments), body.format(**arguments)


def todo_push_snapshot(todo, actor, event):
    return {
        "event_key": "todo_" + event,
        "event_args": {"project": todo.project.name, "todo": todo.title, "actor": actor.nickname or actor.email},
    }
