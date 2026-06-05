# Scheduling with a systemd **user** timer (recommended over cron)

Why prefer this over the cron entry in `RUNBOOK.md`? A `systemd` timer with
`Persistent=true` **runs a missed job as soon as the machine wakes** — so if the
PC was off or asleep at 07:00, the day isn't silently skipped (cron's weakness).

These are **user** units (no root needed). They assume the repo is at `~/immo`
and `scripts/run_lux.sh` is executable (`chmod +x scripts/run_lux.sh`).

## Install (one time)

```bash
mkdir -p ~/.config/systemd/user
cp ~/immo/scripts/systemd/lux-monitor.service ~/.config/systemd/user/
cp ~/immo/scripts/systemd/lux-monitor.timer   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now lux-monitor.timer

# So user timers fire even when you're not logged in (optional but recommended):
sudo loginctl enable-linger "$USER"
```

## Check / operate

```bash
systemctl --user list-timers lux-monitor.timer   # next & last run
systemctl --user status lux-monitor.service      # last run result
journalctl --user -u lux-monitor.service -n 50   # logs (also in ~/immo/logs/lux_run.log)

systemctl --user start lux-monitor.service        # run once now, on demand
systemctl --user disable --now lux-monitor.timer  # stop scheduling
```

## Change the time
Edit `OnCalendar=` in `lux-monitor.timer` (e.g. `*-*-* 06:30:00`, or twice daily
`OnCalendar=*-*-* 07,18:00:00`), then:
```bash
cp ~/immo/scripts/systemd/lux-monitor.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user restart lux-monitor.timer
```

> Use **either** this timer **or** the cron line from `RUNBOOK.md`, not both.
