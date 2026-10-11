"""Shared fallback palette used by the app and independently refreshing widgets."""

PROJECT_PALETTE = (
    "#009688", "#3F51B5", "#FF5722", "#00BCD4", "#9C27B0",
    "#607D8B", "#4CAF50", "#E91E63", "#FFC107",
)


def project_display_color(project, personal_color=""):
    return personal_color or project.color or PROJECT_PALETTE[
        sum(ord(character) for character in str(project.id)) % len(PROJECT_PALETTE)
    ]
