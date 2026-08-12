# Rebuilding Dado's schedule by hand

GENERATED - do not edit. Refreshed by dado_profile_mirror.py from the LIVE
profile, which is the only authoritative copy.

This file exists so a wiped schedule can be rebuilt BY A PERSON. There is
deliberately no importer: hermes rewrites jobs.json on every run under its
own lock, so an external writer can silently drop a claim or a completion,
and copying a mirror over a live schedule is what the neighbouring tree had
to build a refusal gate against.

10 job(s) live at the time of writing:

    hermes -p dado cron create "10 5 * * *" --name dado-conduct-review --no-agent --script conduct_review.py --deliver telegram:891365639
    hermes -p dado cron create "15 8 * * *" --name dado-daily-banking-review --no-agent --script dado_daily_banking_review.py --deliver origin
    hermes -p dado cron create "0 8 * * 1-5" --name dado-followup-digest --no-agent --script dado_followup_digest.py --deliver local
    hermes -p dado cron create "0 7,9,11,13,15,17,19 * * *" --name dado-inbox-watch --no-agent --script dado_inbox_reasoner.py --deliver local
    hermes -p dado cron create "*/10 * * * *" --name dado-job-watch --no-agent --script job_runner.py --deliver local
    hermes -p dado cron create "0 8 1 * *" --name dado-monthly-reorder-review --script zoho_reorder_analysis.py --deliver origin
    hermes -p dado cron create "*/15 * * * *" --name dado-stall-tripwire --no-agent --script stall_tripwire.py --deliver local
    hermes -p dado cron create "*/10 * * * *" --name dado-zoho-session-watch --no-agent --script zoho_session_keepalive.py --deliver telegram:891365639
    hermes -p dado cron create "0 9 * * 1" --name packing-observation-weekly-reminder --no-agent --script packing_observation_weekly_reminder.py --deliver origin
    hermes -p dado cron create "every 30m" --name packing-order-monitor --no-agent --script packing_order_monitor.py --deliver origin
