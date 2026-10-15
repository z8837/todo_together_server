from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("todo_together", "0036_project_user_preference")]
    operations = [
        migrations.AddField(
            model_name="device",
            name="locale",
            field=models.CharField(max_length=10, null=True, blank=True),
        ),
    ]
