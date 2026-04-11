# Generated manually for show_inline_rewrite preference

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userpreferences',
            name='show_inline_rewrite',
            field=models.BooleanField(
                default=True,
                help_text='Whether to show Rewrite on Voice Input and Text Input composer screens',
            ),
        ),
    ]
