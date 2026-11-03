"""Support python -m campus_market as well as the installed console command."""

from campus_market.cli import main

raise SystemExit(main())
