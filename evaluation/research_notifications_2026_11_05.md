# Daily IEX research notifications

Successful research sessions previously produced no routine phone alerts. The
observer now sends a start notification after its first successful data request.
The scheduled finish job sends a summary at approximately 12:35 Eastern, including
cycles, fresh-symbol coverage, eligible signals, errors and available 30-minute
stock-signal movement before costs. Interrupted or degraded sessions are labelled
accordingly. The message does not represent broker profit or options readiness.

Notifications use the existing OPS_NTFY_URL and optional OPS_NTFY_TOKEN. Accepted
messages are deduplicated by event code and Eastern date across restarts and
finish retries. The research watchdog retries failed deliveries even outside the
collection window. Review and publication failures do not suppress the summary.
Market holidays do not generate start or completion messages without a session.

Install an exact descendant release with the existing /root/trader-recover.sh
command. After an after-hours upgrade, /root/auto_close_session.sh can send today's
existing report without restarting collection. The frozen research baseline,
strategy parameters, data provider, entry cutoff and order-disable policy are
unchanged.
