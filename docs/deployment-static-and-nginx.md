# Deploying static files (Tailwind + Django + Nginx)

This guide is for future you: it explains **why** the live site sometimes looks broken (huge icons, plain buttons, wrong fonts) and **what** to run or fix on the server. You do not need to be an expert in Django or Nginx to follow it.

---

## 1. Words you will see

| Term | Plain meaning |
|------|----------------|
| **Static files** | CSS, JavaScript, images. Your Tailwind output (`tailwind.css`) is static. |
| **`STATIC_ROOT`** | The folder where Django **copies** all static files when you run `collectstatic`. In this project it is: `src/staticfiles` (under the project root / `BASE_DIR`). |
| **`STATIC_URL`** | The URL prefix in the browser. Here it is `/static/`, so CSS is loaded like `/static/css/tailwind.css`. |
| **`collectstatic`** | A Django command that **gathers** files from apps and `STATICFILES_DIRS` into `STATIC_ROOT`. It does **not** by itself fix Nginx or permissions. |
| **Tailwind build** | Compiles your design CSS from templates and `input.css` into the file the site actually links to. Must run **before** `collectstatic` in production. |
| **Nginx** | A web server that often sits **in front** of Django. It can serve `/static/` directly from disk, or forward everything to Django. |
| **403 Forbidden** | The server understood the request but **refuses** to serve the file. For static files this is usually **wrong path** or **file/folder permissions**. |
| **WhiteNoise** | Django middleware (already enabled in this project) that can serve static files **from the app** when requests reach Django. If Nginx handles `/static/` first, WhiteNoise is not used for those URLs. |

---

## 2. Why it works on your laptop but breaks in production

On your machine, with **development** settings, Django’s dev server often serves static files automatically when `DEBUG=True`.

In **production**, `DEBUG` is usually `False`. Django **does not** use that dev-only behaviour. You must:

1. Build Tailwind for production.
2. Run `collectstatic` so everything ends up under `STATIC_ROOT`.
3. Make sure **something** can serve URLs under `/static/`:
   - **Option A:** Nginx serves files from `STATIC_ROOT` (common on a VPS), **or**
   - **Option B:** Nginx sends `/static/` to Django and **WhiteNoise** serves them.

If step 3 fails (404, 403, or wrong path), the browser never gets `tailwind.css`. The page still loads, but **without styles**: big SVG icons, default blue links, system fonts.

---

## 3. Every deployment: commands to run (on the server)

Run these **in the project directory**, with your virtualenv activated and production settings (however you set `DJANGO_SETTINGS_MODULE`, e.g. `src.utter_it.settings.prod`).

**Order matters.**

```bash
# 1) Install dependencies (if you just pulled new code)
pip install -r requirements.txt

# 2) Build Tailwind CSS for production
python manage.py tailwind build

# 3) Copy all static files into STATIC_ROOT
python manage.py collectstatic --noinput
```

Then restart your application process (gunicorn, uvicorn, systemd service, etc.) if your host requires it.

---

## 4. Quick check from your own computer

After deploy, check that the main CSS file is reachable (replace with your real domain):

```bash
curl -sI "https://utter-it.com/static/css/tailwind.css"
```

- **`HTTP/1.1 200`** (or **304**) — good; the file is being served.
- **`404`** — wrong path, `collectstatic` not run, or Nginx `alias`/`root` does not match `STATIC_ROOT`.
- **`403 Forbidden`** (often with **Server: nginx**) — file may exist, but Nginx’s user (usually `www-data`) **cannot read** the file or **cannot traverse** a parent folder (very common if static lives under `/home/youruser/...`).

---

## 5. If you get **403** from Nginx (read this slowly)

Nginx needs **permission to walk the full path** from `/` down to `tailwind.css`.

### 5.1 Find where Nginx thinks static files live

On the server:

```bash
sudo grep -R "location" /etc/nginx/sites-enabled/ | grep -i static
```

Open the matching site file and look for a block like:

```nginx
location /static/ {
    alias /some/full/path/...;
}
```

or a `root` directive. That path must be the folder that **contains** `css/tailwind.css` after `collectstatic`. In this project, that should match Django’s `STATIC_ROOT` (the `src/staticfiles` directory at project root).

### 5.2 Confirm the file is really there

```bash
sudo ls -la /full/path/you/found/static/css/tailwind.css
```

If this says “No such file”, fix `collectstatic`, `tailwind build`, or the Nginx path first.

### 5.3 Fix permissions (very common on VPS)

If the path is under `/home/yourname/...`, your home directory might be `700` or `750`, so `www-data` cannot enter it → **403**.

**Good long-term fix:** put collected static in a simple path Nginx is meant to read, for example:

```text
/var/www/utter-it/static/
```

Set `STATIC_ROOT` to that path in production settings **or** run `collectstatic` and then copy `src/staticfiles/*` there, and point Nginx `alias` at that folder.

**Minimal fix:** add “execute” for others on each directory in the chain (only if you understand this exposes directory traversal to the world for those dirs):

```bash
chmod o+x /home/yourname
chmod o+x /home/yourname/app
# ... each folder until you reach staticfiles
```

Then:

```bash
find /path/to/staticfiles -type d -exec chmod 755 {} \;
find /path/to/staticfiles -type f -exec chmod 644 {} \;
```

**Copy-paste trap:** the bit at the end must be **backslash + semicolon** (`\;`), not a plain `;`. The shell treats `;` as “end of command” unless you escape it, and then `find` reports `missing argument to '-exec'`. Correct: `{} \;` — wrong: `{} ;`.

**Alternative without `-exec` (easier to remember):**

```bash
chmod -R u=rwX,go=rX /path/to/staticfiles
```

(`X` means “execute only for directories and files that are already executable,” which is usually what you want for static trees.)

Test Nginx config and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 6. Nginx vs WhiteNoise (simple rule)

- If your Nginx config has a **`location /static/`** that points to disk, **Nginx serves those files**. Fix paths and permissions there.
- If Nginx **only** `proxy_pass`es everything to Django and **no** separate `location /static/`, then **WhiteNoise** (in this project’s `MIDDLEWARE`) can serve static **as long as** `collectstatic` was run and the app is actually handling the request.

You are not supposed to fight both: pick one clear setup and keep `STATIC_ROOT` and Nginx `alias` in sync.

---

## 7. One-page checklist before you call it “deployed”

- [ ] `python manage.py tailwind build` completed without errors  
- [ ] `python manage.py collectstatic --noinput` completed without errors  
- [ ] `STATIC_ROOT` on the server contains `css/tailwind.css`  
- [ ] Nginx `location /static/` (if used) points at that same folder  
- [ ] `curl -sI https://your-domain/static/css/tailwind.css` returns **200**  
- [ ] Open the site in a private/incognito window and confirm layout matches dev  

---

## 8. Where this is configured in code

- **Static settings:** `src/utter_it/settings/base.py` (`STATIC_URL`, `STATIC_ROOT`, `STATICFILES_DIRS`)  
- **Tailwind paths:** same file (`TAILWIND_CLI_SRC_CSS`, `TAILWIND_CLI_DIST_CSS`)  
- **Dev-only static helpers:** `src/utter_it/urls.py` (only when `DEBUG` is True)  
- **WhiteNoise:** `whitenoise.middleware.WhiteNoiseMiddleware` in `MIDDLEWARE` in `base.py`  

If you change `STATIC_ROOT` for production, document it here or in your host’s runbook so Nginx stays aligned.
