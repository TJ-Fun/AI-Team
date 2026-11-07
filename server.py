#!/usr/bin/env python3
"""Personal AI Team local workspace server.

The implementation lives in the ``ait`` package; this file is the entry
point kept for compatibility with the systemd unit and existing docs.
"""

from ait.main import main

if __name__ == "__main__":
    main()
