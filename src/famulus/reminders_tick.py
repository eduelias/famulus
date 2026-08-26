"""Minute-cron entrypoint: fire due reminders.

Host crontab:  * * * * * docker exec famulus python3 -m famulus.reminders_tick
"""
from .builtin.reminders import tick

if __name__ == "__main__":
    for line in tick():
        print(line)
