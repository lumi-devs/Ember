# Ember

Ember is a discord bot for The Aakhri Station. confessions, mod tools, temp vc,
automod, birthdays, booster roles, all that stuff in one place.

## setup

needs python 3.12+. a venv is a good idea.

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in your ids + tokens in .env
python main.py
```

check `.env.example` for the full list of vars. if you add a new key,
add it there with an empty value too.

## layout

```
main.py                  entrypoint, loads every cog under cogs/
cogs/confessions/        confess, replies, reports, bans, channel cleaner
cogs/mods/               staff analytics + promoter stuff
cogs/moderation/         moderate, purge, verify
cogs/tempvc/             temp voice channels + gaming hub
cogs/shadowchat/         anonymous relay thing
cogs/booster_roles/      custom booster roles
cogs/utilities/          afk, dragme, b2 sync, game ping, automod, sticky roles...
cogs/extras/             birthday, welcome, feedback, status...
cogs/channels/           channel helpers
helpers/                 downloaders, github + misc shared bits
addons/                  ported Ember-extras plugins (bank, dashboard, duckdns...)
.old/                    disabled cogs, kept around for reference
data/                    sqlite dbs (gitignored, created on first run)
logs/                    bot.log (gitignored)
```

## notes

- slash commands sync on startup, give it a sec
- confession cards use components v2 so you need a discord.py build
  with layout views
- `data/` and `logs/` are created automatically, no need to mkdir
- music/downloader bits need `ffmpeg` on the box for some formats

## license

AGPL-3.0, see [LICENSE](LICENSE).
