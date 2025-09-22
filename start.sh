#!/bin/bash
cd /home/ubuntu/discord-data || exit 1

# Update from git
/usr/bin/git pull

# Run inside venv
exec /home/ubuntu/venv/bin/python main.py
