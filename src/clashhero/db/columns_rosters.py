"""Roster auto-ping settings (``/roster autoping``), added to the existing ``rosters`` table."""

COLUMNS = [
    # Ping unregistered clan members this many hours before start_time / end_time (NULL = off).
    ("rosters", "autoping_start_hours", "REAL"),
    ("rosters", "autoping_end_hours", "REAL"),
    ("rosters", "autoping_message", "TEXT"),
    ("rosters", "autoping_channel_id", "INTEGER"),
    # The start_time / end_time value last pinged for, so each window pings once. Editing the
    # roster's time re-arms the ping.
    ("rosters", "autoping_start_sent", "TEXT"),
    ("rosters", "autoping_end_sent", "TEXT"),
]
