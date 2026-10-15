# discord-data

discord bot for The Aakhri Station. confessions, mod tools, temp vc,
automod, birthdays, booster roles, all that stuff in one place.

## setup

needs python 3.12+. a venv is a good idea.

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in your ids + tokens in .env
python main.py
```

never commit `.env`. it's gitignored. if you add a new key,
add it to `.env.example` with an empty value too.

all the secrets live in env vars now: `BOT_TOKEN`, `GITHUB_TOKEN`,
`GROQ_API_KEY`, `OPENAI_API_KEY`, `HF_TOKEN`, `B2_APP_KEY`,
`REDDIT_CLIENT_ID/SECRET`, `IG_USER/IG_PASS`. nothing hardcoded.

## how confessions stay anonymous

- confessions + replies store the real user id in sqlite (`data/confession.db`)
  so limits, bans and threads work
- mods only ever see a sha256 hash of the id in `MOD_CONFESSION_LOGS_CHANNEL_ID`
- there is no channel that shows real user ids for confessions. that used
  to exist (`LOG_CHANNEL_ID`) and it got removed on purpose, don't add it back
- reports go to `REPORT_CHANNEL_ID` with the author shown as a hash.
  the reporter is visible so abuse can be dealt with

`/confessban` takes a mention, a user id, or one of those hashes.
`/confess_delete` deletes + renumbers so ids don't have gaps.

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

MIT, see [LICENSE](LICENSE).
