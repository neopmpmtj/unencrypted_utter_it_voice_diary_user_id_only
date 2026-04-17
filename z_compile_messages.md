### Compile on production

On the server, install gettext first:

```bash
sudo apt-get install -y gettext
python manage.py compilemessages --ignore=.venv
```