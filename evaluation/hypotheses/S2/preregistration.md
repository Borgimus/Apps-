# S2 | Failed opening-range breakdown

Use SPY and QQQ for the primary test. IWM and DIA are a separate diagnostic.
The mechanism is possible absorption after a failed selloff. Require all three
09:30–09:45 five-minute bars. A completed bar must close below the opening
low; a later completed bar closes above the low within three immediately
following grid bars. All intervening bars must be present. The first completed
reclaim per symbol/day is eligible at its actual availability, by 11:30 ET.

Primary control is a long at the frozen ORB short discovery time on the same
symbol set, with the frozen ORB still imported under frozen parameters.
Secondary controls are the frozen ORB long arm and the protocol's seeded
same-clock non-signal-day entry. Primary comparison is S2 minus inverse ORB
at 30 minutes and 10 bps. Also record maximum adverse excursion. No stop
assumptions. Use all shared pass and kill thresholds.
