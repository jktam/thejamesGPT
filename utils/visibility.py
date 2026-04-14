from discord import app_commands

VISIBILITY_CHOICES = [
    app_commands.Choice(name="public", value="public"),
    app_commands.Choice(name="private", value="private"),
]


def is_ephemeral(visibility: app_commands.Choice[str] | None, default_private: bool) -> bool:
    if visibility is None:
        return default_private
    return visibility.value == "private"